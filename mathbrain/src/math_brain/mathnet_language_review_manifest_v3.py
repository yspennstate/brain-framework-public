"""Replay-bound dual language-review manifests for the MathNet pilot universe.

The child-visible contract contains only HMAC item IDs and raw problem text.  Signed
manifests contain only IDs and categorical verdicts.  Signatures authenticate exact bytes
under externally expected keys; they do not prove runtime isolation, reviewer independence,
chronology, or the correctness of a verdict.  Consequently every serialized artifact in
this module remains selector-ineligible.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from .canonical_ed25519_v3 import (
    CanonicalEd25519SignerV3,
    CanonicalEd25519VerifierV3,
)
from .mathnet_acquisition_v3 import (
    PreparedMathNetPilotEvaluatorVaultV3,
    PreparedMathNetPilotUniverseV3,
    PreparedPinnedMathNetSourceV3,
)
from .mathnet_language_audit_projection_v3 import (
    MATHNET_LANGUAGE_AUDIT_MAX_ITEMS_V3,
    UnsignedMathNetAuditProjectionV3,
    _pseudonym_key_commitment_v3,
    build_unsigned_mathnet_language_audit_projection_v3,
)
from .models import canonical_json

MATHNET_LANGUAGE_REVIEW_OUTPUT_SCHEMA_V3 = (
    "org.math-research-brain/mathnet-language-review-decisions"
)
MATHNET_LANGUAGE_REVIEW_MANIFEST_SCHEMA_V3 = (
    "org.math-research-brain/mathnet-language-review-manifest"
)
MATHNET_LANGUAGE_REVIEW_REPORT_SCHEMA_V3 = (
    "org.math-research-brain/mathnet-language-review-resolution"
)
MATHNET_LANGUAGE_REVIEW_VERSION_V3 = 3
MATHNET_LANGUAGE_REVIEW_MAX_JSON_BYTES_V3 = 16 * 1024 * 1024
MATHNET_LANGUAGE_REVIEW_MAX_STDOUT_BYTES_V3 = 2 * 1024 * 1024
MATHNET_LANGUAGE_REVIEW_SCHEMA_DIALECT_V3 = (
    "https://json-schema.org/draft/2020-12/schema"
)

MATHNET_REVIEWER_A_DOMAIN_V3 = (
    "org.math-research-brain/mathnet-language-review/reviewer-a/v3"
)
MATHNET_REVIEWER_B_DOMAIN_V3 = (
    "org.math-research-brain/mathnet-language-review/reviewer-b/v3"
)

_AUDIT_ITEM_ID_RE = re.compile(r"mathnet-language-audit:[0-9a-f]{64}\Z")
_DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}\Z")
_SIGNATURE_RE = re.compile(r"ed25519:[0-9a-f]{128}\Z")
_DECISION_UNIVERSE_DOMAIN = (
    "org.math-research-brain/mathnet-language-review-decision-universe/v3"
)
_MANIFEST_CONTENT_DOMAIN = (
    "org.math-research-brain/mathnet-language-review-manifest/v3"
)
_REPORT_CONTENT_DOMAIN = (
    "org.math-research-brain/mathnet-language-review-resolution/v3"
)


class MathNetLanguageReviewVerdictV3(StrEnum):
    ENGLISH = "english"
    NON_ENGLISH = "non_english"
    UNCERTAIN = "uncertain"


class MathNetLanguageReviewerRoleV3(StrEnum):
    REVIEWER_A = "reviewer_a"
    REVIEWER_B = "reviewer_b"


class MathNetLanguageResolutionCodeV3(StrEnum):
    AGREED_ENGLISH = "agreed_english"
    AGREED_NON_ENGLISH = "agreed_non_english"
    UNCERTAIN = "uncertain"
    DISAGREEMENT = "disagreement"


def _digest(value: object, *, domain: str) -> str:
    return "sha256:" + hashlib.sha256(
        canonical_json({"domain": domain, "payload": value}).encode("utf-8")
    ).hexdigest()


def _validate_digest(value: object, label: str) -> str:
    if type(value) is not str or _DIGEST_RE.fullmatch(value) is None:
        raise ValueError(f"{label} must be a canonical SHA-256 digest")
    return value


def _mapping(value: object, label: str) -> dict[str, Any]:
    if type(value) is not dict or any(type(key) is not str for key in value):
        raise ValueError(f"{label} must be an exact string-keyed object")
    return value


def _exact_keys(value: dict[str, Any], expected: set[str], label: str) -> None:
    if set(value) != expected:
        raise ValueError(f"{label} has unexpected fields")


def _exact_integer(
    value: object,
    label: str,
    *,
    minimum: int,
    maximum: int,
) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f"{label} must be an exact integer in [{minimum}, {maximum}]")
    return value


def _reject_duplicate_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"duplicate JSON key: {key}")
        value[key] = item
    return value


def _reject_nonfinite(value: str) -> None:
    raise ValueError(f"non-finite JSON constant is forbidden: {value}")


def _load_canonical_text(raw: object, label: str, *, maximum: int) -> object:
    if type(raw) is not str:
        raise ValueError(f"{label} JSON must be exact text")
    try:
        encoded = raw.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValueError(f"{label} JSON must be valid UTF-8") from exc
    if len(encoded) > maximum:
        raise ValueError(f"{label} JSON exceeds its byte budget")
    try:
        value = json.loads(
            raw,
            object_pairs_hook=_reject_duplicate_pairs,
            parse_constant=_reject_nonfinite,
        )
    except (json.JSONDecodeError, RecursionError, ValueError) as exc:
        raise ValueError(f"{label} JSON is invalid") from exc
    if raw != canonical_json(value):
        raise ValueError(f"{label} JSON is not canonical")
    return value


def _reviewer_payload_digest_v3(projection: UnsignedMathNetAuditProjectionV3) -> str:
    if type(projection) is not UnsignedMathNetAuditProjectionV3:
        raise ValueError("language review requires an exact unsigned projection")
    projection.validate_current()
    public = projection.public_commitment_payload()
    return _validate_digest(
        public.get("reviewer_payload_digest"),
        "MathNet reviewer payload digest",
    )


def mathnet_language_review_stdin_v3(
    projection: UnsignedMathNetAuditProjectionV3,
) -> bytes:
    """Return the exact child-visible payload plus its single framing newline."""

    if type(projection) is not UnsignedMathNetAuditProjectionV3:
        raise ValueError("language-review stdin requires an exact unsigned projection")
    raw = projection.reviewer_payload_json().encode("utf-8") + b"\n"
    if len(raw) > MATHNET_LANGUAGE_REVIEW_MAX_JSON_BYTES_V3:
        raise ValueError("MathNet language-review stdin exceeds its byte budget")
    return raw


def mathnet_language_review_output_schema_v3(
    projection: UnsignedMathNetAuditProjectionV3,
) -> dict[str, object]:
    """Build the strict output schema without exposing prompts or evaluator metadata."""

    if type(projection) is not UnsignedMathNetAuditProjectionV3:
        raise ValueError("language-review schema requires an exact unsigned projection")
    projection.validate_current()
    item_ids = [item.audit_item_id for item in projection.items]
    verdict_schema = {
        "enum": [verdict.value for verdict in MathNetLanguageReviewVerdictV3],
        "type": "string",
    }
    positional_decisions = [
        {
            "additionalProperties": False,
            "properties": {
                "audit_item_id": {"const": item_id, "type": "string"},
                "verdict": verdict_schema,
            },
            "required": ["audit_item_id", "verdict"],
            "type": "object",
        }
        for item_id in item_ids
    ]
    return {
        "$schema": MATHNET_LANGUAGE_REVIEW_SCHEMA_DIALECT_V3,
        "additionalProperties": False,
        "properties": {
            "decisions": {
                "items": False,
                "maxItems": len(item_ids),
                "minItems": len(item_ids),
                "prefixItems": positional_decisions,
                "type": "array",
                "uniqueItems": True,
            },
            "schema": {
                "const": MATHNET_LANGUAGE_REVIEW_OUTPUT_SCHEMA_V3,
                "type": "string",
            },
            "schema_version": {
                "const": MATHNET_LANGUAGE_REVIEW_VERSION_V3,
                "type": "integer",
            },
        },
        "required": ["schema", "schema_version", "decisions"],
        "type": "object",
    }


@dataclass(frozen=True, slots=True)
class MathNetLanguageReviewDecisionV3:
    audit_item_id: str
    verdict: MathNetLanguageReviewVerdictV3

    def __post_init__(self) -> None:
        if type(self) is not MathNetLanguageReviewDecisionV3:
            raise ValueError("MathNet language-review decision subclasses are forbidden")
        if type(self.audit_item_id) is not str or _AUDIT_ITEM_ID_RE.fullmatch(
            self.audit_item_id
        ) is None:
            raise ValueError("MathNet language-review decision id is invalid")
        if type(self.verdict) is not MathNetLanguageReviewVerdictV3:
            raise ValueError("MathNet language-review verdict must be exact")

    def to_dict(self) -> dict[str, str]:
        self.__post_init__()
        return {"audit_item_id": self.audit_item_id, "verdict": self.verdict.value}

    @classmethod
    def from_dict(cls, raw: object) -> MathNetLanguageReviewDecisionV3:
        value = _mapping(raw, "MathNet language-review decision")
        _exact_keys(value, {"audit_item_id", "verdict"}, "language-review decision")
        try:
            verdict = MathNetLanguageReviewVerdictV3(value["verdict"])
        except (TypeError, ValueError) as exc:
            raise ValueError("serialized MathNet language-review verdict is invalid") from exc
        return cls(audit_item_id=value["audit_item_id"], verdict=verdict)


def _validate_decisions_v3(
    decisions: object,
) -> tuple[MathNetLanguageReviewDecisionV3, ...]:
    if type(decisions) is not tuple or not 1 <= len(decisions) <= (
        MATHNET_LANGUAGE_AUDIT_MAX_ITEMS_V3
    ):
        raise ValueError("MathNet language-review decisions must be a bounded exact tuple")
    if any(type(item) is not MathNetLanguageReviewDecisionV3 for item in decisions):
        raise ValueError("MathNet language-review decisions must be exact")
    for item in decisions:
        item.__post_init__()
    ids = tuple(item.audit_item_id for item in decisions)
    if ids != tuple(sorted(ids)) or len(set(ids)) != len(ids):
        raise ValueError("MathNet language-review decision ids must be unique and sorted")
    return decisions


def _decision_universe_digest_v3(
    decisions: tuple[MathNetLanguageReviewDecisionV3, ...],
) -> str:
    return _digest(
        [item.to_dict() for item in decisions],
        domain=_DECISION_UNIVERSE_DOMAIN,
    )


_REVIEW_SUBMISSION_TOKEN_V3 = object()


@dataclass(frozen=True, slots=True, init=False)
class MathNetLanguageReviewSubmissionV3:
    reviewer_role: MathNetLanguageReviewerRoleV3
    decisions: tuple[MathNetLanguageReviewDecisionV3, ...] = field(repr=False)
    review_runtime_evidence_digest: str
    projection_content_digest: str
    reviewer_payload_digest: str
    pseudonym_key_commitment: str
    decision_universe_digest: str
    _token: object = field(repr=False, compare=False)

    def __new__(cls, token: object = None) -> MathNetLanguageReviewSubmissionV3:
        if token is not _REVIEW_SUBMISSION_TOKEN_V3:
            raise TypeError("MathNet language-review submissions require the strict parser")
        return object.__new__(cls)

    def __init__(self, token: object = None) -> None:
        del token
        raise TypeError("MathNet language-review submissions require the strict parser")

    def _validate_input_fields(self) -> None:
        if type(self) is not MathNetLanguageReviewSubmissionV3:
            raise ValueError("MathNet language-review submission subclasses are forbidden")
        if type(self.reviewer_role) is not MathNetLanguageReviewerRoleV3:
            raise ValueError("MathNet language-review role must be exact")
        _validate_decisions_v3(self.decisions)
        _validate_digest(
            self.review_runtime_evidence_digest,
            "MathNet language-review runtime evidence digest",
        )
        _validate_digest(
            self.projection_content_digest,
            "MathNet language-review projection digest",
        )
        _validate_digest(
            self.reviewer_payload_digest,
            "MathNet language-review payload digest",
        )
        _validate_digest(
            self.pseudonym_key_commitment,
            "MathNet language-review pseudonym-key commitment",
        )

    @classmethod
    def _derive(
        cls,
        *,
        reviewer_role: MathNetLanguageReviewerRoleV3,
        decisions: tuple[MathNetLanguageReviewDecisionV3, ...],
        review_runtime_evidence_digest: str,
        projection_content_digest: str,
        reviewer_payload_digest: str,
        pseudonym_key_commitment: str,
    ) -> MathNetLanguageReviewSubmissionV3:
        instance = object.__new__(cls)
        object.__setattr__(instance, "reviewer_role", reviewer_role)
        object.__setattr__(instance, "decisions", decisions)
        object.__setattr__(
            instance,
            "review_runtime_evidence_digest",
            review_runtime_evidence_digest,
        )
        object.__setattr__(instance, "projection_content_digest", projection_content_digest)
        object.__setattr__(instance, "reviewer_payload_digest", reviewer_payload_digest)
        object.__setattr__(instance, "pseudonym_key_commitment", pseudonym_key_commitment)
        object.__setattr__(
            instance,
            "decision_universe_digest",
            _decision_universe_digest_v3(decisions),
        )
        object.__setattr__(instance, "_token", _REVIEW_SUBMISSION_TOKEN_V3)
        instance.validate_current()
        return instance

    def validate_current(self) -> None:
        if self._token is not _REVIEW_SUBMISSION_TOKEN_V3:
            raise ValueError("MathNet language-review submission was not produced by the parser")
        self._validate_input_fields()
        supplied = _validate_digest(
            self.decision_universe_digest,
            "MathNet language-review decision-universe digest",
        )
        expected = _decision_universe_digest_v3(self.decisions)
        if not hmac.compare_digest(supplied, expected):
            raise ValueError("MathNet language-review submission decision digest is stale")

    def __reduce_ex__(self, protocol: int) -> object:
        del protocol
        raise TypeError("MathNet language-review submissions are process-local parser results")


def parse_mathnet_language_review_stdout_v3(
    raw: bytes,
    projection: UnsignedMathNetAuditProjectionV3,
    *,
    reviewer_role: MathNetLanguageReviewerRoleV3,
    review_runtime_evidence_digest: str,
) -> MathNetLanguageReviewSubmissionV3:
    """Parse one all-or-nothing canonical child response framed by exactly one LF."""

    if type(projection) is not UnsignedMathNetAuditProjectionV3:
        raise ValueError("language-review parsing requires an exact unsigned projection")
    projection.validate_current()
    if type(raw) is not bytes:
        raise ValueError("MathNet language-review stdout must be immutable bytes")
    if not raw.endswith(b"\n") or len(raw) > MATHNET_LANGUAGE_REVIEW_MAX_STDOUT_BYTES_V3:
        raise ValueError("MathNet language-review stdout framing or byte budget is invalid")
    try:
        body = raw[:-1].decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise ValueError("MathNet language-review stdout must be valid UTF-8") from exc
    parsed = _mapping(
        _load_canonical_text(
            body,
            "MathNet language-review stdout",
            maximum=MATHNET_LANGUAGE_REVIEW_MAX_STDOUT_BYTES_V3,
        ),
        "MathNet language-review stdout",
    )
    _exact_keys(parsed, {"schema", "schema_version", "decisions"}, "review stdout")
    if parsed["schema"] != MATHNET_LANGUAGE_REVIEW_OUTPUT_SCHEMA_V3 or type(
        parsed["schema"]
    ) is not str:
        raise ValueError("MathNet language-review stdout schema is unsupported")
    _exact_integer(
        parsed["schema_version"],
        "MathNet language-review stdout schema version",
        minimum=MATHNET_LANGUAGE_REVIEW_VERSION_V3,
        maximum=MATHNET_LANGUAGE_REVIEW_VERSION_V3,
    )
    raw_decisions = parsed["decisions"]
    if type(raw_decisions) is not list:
        raise ValueError("MathNet language-review stdout decisions must be an exact array")
    submission = MathNetLanguageReviewSubmissionV3._derive(
        reviewer_role=reviewer_role,
        decisions=tuple(
            MathNetLanguageReviewDecisionV3.from_dict(item) for item in raw_decisions
        ),
        review_runtime_evidence_digest=review_runtime_evidence_digest,
        projection_content_digest=projection.content_digest,
        reviewer_payload_digest=_reviewer_payload_digest_v3(projection),
        pseudonym_key_commitment=projection.pseudonym_key_commitment,
    )
    _validate_submission_against_projection_v3(submission, projection)
    return submission


def _manifest_fixed_payload_v3() -> dict[str, object]:
    return {
        "engineering_pilot_only": True,
        "external_anchor_verified_in_artifact": False,
        "independent_peer_review_verified": False,
        "local_source_replay_required_for_use": True,
        "private_gold_present": False,
        "runtime_isolation_verified_in_artifact": False,
        "schema": MATHNET_LANGUAGE_REVIEW_MANIFEST_SCHEMA_V3,
        "schema_version": MATHNET_LANGUAGE_REVIEW_VERSION_V3,
        "selector_handoff_authorized": False,
        "structured_source_metadata_present": False,
        "unsigned_projection_transport": True,
        "wrapper_self_authenticating": False,
    }


def _manifest_payload_v3(
    *,
    reviewer_role: MathNetLanguageReviewerRoleV3,
    projection_content_digest: str,
    reviewer_payload_digest: str,
    pseudonym_key_commitment: str,
    decisions: tuple[MathNetLanguageReviewDecisionV3, ...],
    decision_universe_digest: str,
    review_runtime_evidence_digest: str,
    reviewer_authority_commitment: str,
    reviewer_key_fingerprint: str,
) -> dict[str, object]:
    return _manifest_fixed_payload_v3() | {
        "decision_universe_digest": decision_universe_digest,
        "decisions": [item.to_dict() for item in decisions],
        "item_count": len(decisions),
        "projection_content_digest": projection_content_digest,
        "pseudonym_key_commitment": pseudonym_key_commitment,
        "review_runtime_evidence_digest": review_runtime_evidence_digest,
        "reviewer_authority_commitment": reviewer_authority_commitment,
        "reviewer_key_fingerprint": reviewer_key_fingerprint,
        "reviewer_payload_digest": reviewer_payload_digest,
        "reviewer_role": reviewer_role.value,
    }


@dataclass(frozen=True, slots=True)
class SignedMathNetLanguageReviewManifestV3:
    reviewer_role: MathNetLanguageReviewerRoleV3
    projection_content_digest: str
    reviewer_payload_digest: str
    pseudonym_key_commitment: str
    decisions: tuple[MathNetLanguageReviewDecisionV3, ...] = field(repr=False)
    decision_universe_digest: str
    review_runtime_evidence_digest: str
    reviewer_authority_commitment: str
    reviewer_key_fingerprint: str
    content_digest: str
    authority_signature: str = field(repr=False)

    def __post_init__(self) -> None:
        if type(self) is not SignedMathNetLanguageReviewManifestV3:
            raise ValueError("signed MathNet language-review subclasses are forbidden")
        self._validate_fields()

    def _validate_fields(self) -> None:
        if type(self.reviewer_role) is not MathNetLanguageReviewerRoleV3:
            raise ValueError("signed MathNet language-review role must be exact")
        _validate_decisions_v3(self.decisions)
        for label, value in (
            ("projection digest", self.projection_content_digest),
            ("reviewer payload digest", self.reviewer_payload_digest),
            ("pseudonym-key commitment", self.pseudonym_key_commitment),
            ("decision-universe digest", self.decision_universe_digest),
            ("runtime evidence digest", self.review_runtime_evidence_digest),
            ("reviewer authority commitment", self.reviewer_authority_commitment),
            ("reviewer key fingerprint", self.reviewer_key_fingerprint),
            ("manifest content digest", self.content_digest),
        ):
            _validate_digest(value, f"signed MathNet language-review {label}")
        expected_decisions = _decision_universe_digest_v3(self.decisions)
        if not hmac.compare_digest(expected_decisions, self.decision_universe_digest):
            raise ValueError("signed MathNet language-review decision digest is stale")
        expected_content = _digest(
            self._payload_without_digest(),
            domain=_MANIFEST_CONTENT_DOMAIN,
        )
        if not hmac.compare_digest(expected_content, self.content_digest):
            raise ValueError("signed MathNet language-review content digest is stale")
        if type(self.authority_signature) is not str or _SIGNATURE_RE.fullmatch(
            self.authority_signature
        ) is None:
            raise ValueError("signed MathNet language-review signature is invalid")

    def _payload_without_digest(self) -> dict[str, object]:
        return _manifest_payload_v3(
            reviewer_role=self.reviewer_role,
            projection_content_digest=self.projection_content_digest,
            reviewer_payload_digest=self.reviewer_payload_digest,
            pseudonym_key_commitment=self.pseudonym_key_commitment,
            decisions=self.decisions,
            decision_universe_digest=self.decision_universe_digest,
            review_runtime_evidence_digest=self.review_runtime_evidence_digest,
            reviewer_authority_commitment=self.reviewer_authority_commitment,
            reviewer_key_fingerprint=self.reviewer_key_fingerprint,
        )

    def signing_record(self) -> dict[str, object]:
        self._validate_fields()
        return self._payload_without_digest() | {"content_digest": self.content_digest}

    def signing_record_bytes(self) -> bytes:
        return canonical_json(self.signing_record()).encode("utf-8")

    def validate_current(self) -> None:
        self._validate_fields()

    def to_dict(self) -> dict[str, object]:
        return self.signing_record() | {"authority_signature": self.authority_signature}

    def canonical_json(self) -> str:
        encoded = canonical_json(self.to_dict())
        if len(encoded.encode("utf-8")) > MATHNET_LANGUAGE_REVIEW_MAX_JSON_BYTES_V3:
            raise ValueError("signed MathNet language-review manifest exceeds its byte budget")
        return encoded

    @classmethod
    def from_dict(cls, raw: object) -> SignedMathNetLanguageReviewManifestV3:
        value = _mapping(raw, "signed MathNet language-review manifest")
        fixed = _manifest_fixed_payload_v3()
        expected = set(fixed) | {
            "authority_signature",
            "content_digest",
            "decision_universe_digest",
            "decisions",
            "item_count",
            "projection_content_digest",
            "pseudonym_key_commitment",
            "review_runtime_evidence_digest",
            "reviewer_authority_commitment",
            "reviewer_key_fingerprint",
            "reviewer_payload_digest",
            "reviewer_role",
        }
        _exact_keys(value, expected, "signed language-review manifest")
        for key, fixed_value in fixed.items():
            if value[key] != fixed_value or type(value[key]) is not type(fixed_value):
                raise ValueError(f"signed language-review fixed field {key} differs")
        try:
            role = MathNetLanguageReviewerRoleV3(value["reviewer_role"])
        except (TypeError, ValueError) as exc:
            raise ValueError("serialized MathNet language-review role is invalid") from exc
        raw_decisions = value["decisions"]
        if type(raw_decisions) is not list:
            raise ValueError("serialized MathNet language-review decisions must be an array")
        decisions = tuple(
            MathNetLanguageReviewDecisionV3.from_dict(item) for item in raw_decisions
        )
        item_count = _exact_integer(
            value["item_count"],
            "serialized MathNet language-review item count",
            minimum=1,
            maximum=MATHNET_LANGUAGE_AUDIT_MAX_ITEMS_V3,
        )
        if item_count != len(decisions):
            raise ValueError("serialized MathNet language-review item count differs")
        return cls(
            reviewer_role=role,
            projection_content_digest=value["projection_content_digest"],
            reviewer_payload_digest=value["reviewer_payload_digest"],
            pseudonym_key_commitment=value["pseudonym_key_commitment"],
            decisions=decisions,
            decision_universe_digest=value["decision_universe_digest"],
            review_runtime_evidence_digest=value["review_runtime_evidence_digest"],
            reviewer_authority_commitment=value["reviewer_authority_commitment"],
            reviewer_key_fingerprint=value["reviewer_key_fingerprint"],
            content_digest=value["content_digest"],
            authority_signature=value["authority_signature"],
        )

    @classmethod
    def from_json(cls, raw: object) -> SignedMathNetLanguageReviewManifestV3:
        return cls.from_dict(
            _load_canonical_text(
                raw,
                "signed MathNet language-review manifest",
                maximum=MATHNET_LANGUAGE_REVIEW_MAX_JSON_BYTES_V3,
            )
        )

    def __reduce_ex__(self, protocol: int) -> object:
        del protocol
        raise TypeError("signed MathNet language-review manifests require canonical JSON")


class MathNetReviewerAVerifierV3:
    __slots__ = ("_verifier",)

    def __init__(self, public_key_bytes: bytes) -> None:
        if type(self) is not MathNetReviewerAVerifierV3:
            raise ValueError("MathNet reviewer-A verifier subclasses are forbidden")
        self._verifier = CanonicalEd25519VerifierV3(
            public_key_bytes,
            domain=MATHNET_REVIEWER_A_DOMAIN_V3,
        )

    @property
    def authority_commitment(self) -> str:
        return self._verifier.authority_commitment

    @property
    def key_fingerprint(self) -> str:
        return self._verifier.key_fingerprint

    def public_key_bytes(self) -> bytes:
        return self._verifier.public_key_bytes()

    def _verify(self, manifest: SignedMathNetLanguageReviewManifestV3) -> None:
        self._verifier.verify(manifest.signing_record_bytes(), manifest.authority_signature)


class MathNetReviewerBVerifierV3:
    __slots__ = ("_verifier",)

    def __init__(self, public_key_bytes: bytes) -> None:
        if type(self) is not MathNetReviewerBVerifierV3:
            raise ValueError("MathNet reviewer-B verifier subclasses are forbidden")
        self._verifier = CanonicalEd25519VerifierV3(
            public_key_bytes,
            domain=MATHNET_REVIEWER_B_DOMAIN_V3,
        )

    @property
    def authority_commitment(self) -> str:
        return self._verifier.authority_commitment

    @property
    def key_fingerprint(self) -> str:
        return self._verifier.key_fingerprint

    def public_key_bytes(self) -> bytes:
        return self._verifier.public_key_bytes()

    def _verify(self, manifest: SignedMathNetLanguageReviewManifestV3) -> None:
        self._verifier.verify(manifest.signing_record_bytes(), manifest.authority_signature)


class MathNetReviewerASignerV3:
    __slots__ = ("_signer",)

    def __init__(self, private_seed: bytes) -> None:
        if type(self) is not MathNetReviewerASignerV3:
            raise ValueError("MathNet reviewer-A signer subclasses are forbidden")
        self._signer = CanonicalEd25519SignerV3(
            private_seed,
            domain=MATHNET_REVIEWER_A_DOMAIN_V3,
        )

    @property
    def authority_commitment(self) -> str:
        return self._signer.authority_commitment

    @property
    def key_fingerprint(self) -> str:
        return self._signer.key_fingerprint

    def verifier(self) -> MathNetReviewerAVerifierV3:
        return MathNetReviewerAVerifierV3(self._signer.verifier().public_key_bytes())

    def _sign(self, record: bytes) -> str:
        return self._signer.sign(record)


class MathNetReviewerBSignerV3:
    __slots__ = ("_signer",)

    def __init__(self, private_seed: bytes) -> None:
        if type(self) is not MathNetReviewerBSignerV3:
            raise ValueError("MathNet reviewer-B signer subclasses are forbidden")
        self._signer = CanonicalEd25519SignerV3(
            private_seed,
            domain=MATHNET_REVIEWER_B_DOMAIN_V3,
        )

    @property
    def authority_commitment(self) -> str:
        return self._signer.authority_commitment

    @property
    def key_fingerprint(self) -> str:
        return self._signer.key_fingerprint

    def verifier(self) -> MathNetReviewerBVerifierV3:
        return MathNetReviewerBVerifierV3(self._signer.verifier().public_key_bytes())

    def _sign(self, record: bytes) -> str:
        return self._signer.sign(record)


def _validate_submission_against_projection_v3(
    submission: MathNetLanguageReviewSubmissionV3,
    projection: UnsignedMathNetAuditProjectionV3,
) -> None:
    if type(submission) is not MathNetLanguageReviewSubmissionV3:
        raise ValueError("language-review signing requires an exact parser submission")
    if type(projection) is not UnsignedMathNetAuditProjectionV3:
        raise ValueError("language-review signing requires an exact unsigned projection")
    submission.validate_current()
    projection.validate_current()
    for label, observed, expected in (
        (
            "projection content digest",
            submission.projection_content_digest,
            projection.content_digest,
        ),
        (
            "reviewer payload digest",
            submission.reviewer_payload_digest,
            _reviewer_payload_digest_v3(projection),
        ),
        (
            "pseudonym-key commitment",
            submission.pseudonym_key_commitment,
            projection.pseudonym_key_commitment,
        ),
    ):
        if not hmac.compare_digest(observed, expected):
            raise ValueError(f"MathNet language-review submission {label} differs")
    expected = tuple(item.audit_item_id for item in projection.items)
    observed = tuple(item.audit_item_id for item in submission.decisions)
    if observed != expected:
        raise ValueError("MathNet language-review decisions do not exactly cover the projection")


def sign_replayed_mathnet_language_review_manifest_v3(
    source: PreparedPinnedMathNetSourceV3,
    prepared_universe: PreparedMathNetPilotUniverseV3,
    evaluator_vault: PreparedMathNetPilotEvaluatorVaultV3,
    submission: MathNetLanguageReviewSubmissionV3,
    *,
    vault_key: bytes,
    pseudonym_key: bytes,
    signer: MathNetReviewerASignerV3 | MathNetReviewerBSignerV3,
    batch_size: int = 512,
) -> SignedMathNetLanguageReviewManifestV3:
    """Replay the source itself before signing one complete categorical review."""

    if type(submission) is not MathNetLanguageReviewSubmissionV3:
        raise ValueError("language-review signing requires an exact submission")
    submission.validate_current()
    role = submission.reviewer_role
    if role is MathNetLanguageReviewerRoleV3.REVIEWER_A:
        if type(signer) is not MathNetReviewerASignerV3:
            raise ValueError("reviewer-A submission requires the reviewer-A signer")
    elif type(signer) is not MathNetReviewerBSignerV3:
        raise ValueError("reviewer-B submission requires the reviewer-B signer")

    projection = build_unsigned_mathnet_language_audit_projection_v3(
        source,
        prepared_universe,
        evaluator_vault,
        vault_key=vault_key,
        pseudonym_key=pseudonym_key,
        batch_size=batch_size,
    )
    _validate_submission_against_projection_v3(submission, projection)
    reviewer_payload_digest = _reviewer_payload_digest_v3(projection)
    unsigned = _manifest_payload_v3(
        reviewer_role=role,
        projection_content_digest=projection.content_digest,
        reviewer_payload_digest=reviewer_payload_digest,
        pseudonym_key_commitment=projection.pseudonym_key_commitment,
        decisions=submission.decisions,
        decision_universe_digest=submission.decision_universe_digest,
        review_runtime_evidence_digest=submission.review_runtime_evidence_digest,
        reviewer_authority_commitment=signer.authority_commitment,
        reviewer_key_fingerprint=signer.key_fingerprint,
    )
    content_digest = _digest(unsigned, domain=_MANIFEST_CONTENT_DOMAIN)
    signing_record = canonical_json(unsigned | {"content_digest": content_digest}).encode(
        "utf-8"
    )
    manifest = SignedMathNetLanguageReviewManifestV3(
        reviewer_role=role,
        projection_content_digest=projection.content_digest,
        reviewer_payload_digest=reviewer_payload_digest,
        pseudonym_key_commitment=projection.pseudonym_key_commitment,
        decisions=submission.decisions,
        decision_universe_digest=submission.decision_universe_digest,
        review_runtime_evidence_digest=submission.review_runtime_evidence_digest,
        reviewer_authority_commitment=signer.authority_commitment,
        reviewer_key_fingerprint=signer.key_fingerprint,
        content_digest=content_digest,
        authority_signature=signer._sign(signing_record),
    )
    manifest.validate_current()
    return manifest


@dataclass(frozen=True, slots=True)
class MathNetLanguageReviewResolutionItemV3:
    audit_item_id: str
    reviewer_a_verdict: MathNetLanguageReviewVerdictV3
    reviewer_b_verdict: MathNetLanguageReviewVerdictV3
    resolution: MathNetLanguageResolutionCodeV3

    def __post_init__(self) -> None:
        if type(self) is not MathNetLanguageReviewResolutionItemV3:
            raise ValueError("MathNet language-resolution item subclasses are forbidden")
        if type(self.audit_item_id) is not str or _AUDIT_ITEM_ID_RE.fullmatch(
            self.audit_item_id
        ) is None:
            raise ValueError("MathNet language-resolution item id is invalid")
        if type(self.reviewer_a_verdict) is not MathNetLanguageReviewVerdictV3 or type(
            self.reviewer_b_verdict
        ) is not MathNetLanguageReviewVerdictV3:
            raise ValueError("MathNet language-resolution verdicts must be exact")
        if type(self.resolution) is not MathNetLanguageResolutionCodeV3:
            raise ValueError("MathNet language-resolution code must be exact")
        expected = _resolution_code_v3(
            self.reviewer_a_verdict,
            self.reviewer_b_verdict,
        )
        if self.resolution is not expected:
            raise ValueError("MathNet language-resolution code differs from its verdicts")

    def to_dict(self) -> dict[str, str]:
        self.__post_init__()
        return {
            "audit_item_id": self.audit_item_id,
            "resolution": self.resolution.value,
            "reviewer_a_verdict": self.reviewer_a_verdict.value,
            "reviewer_b_verdict": self.reviewer_b_verdict.value,
        }

    @classmethod
    def from_dict(cls, raw: object) -> MathNetLanguageReviewResolutionItemV3:
        value = _mapping(raw, "MathNet language-resolution item")
        _exact_keys(
            value,
            {
                "audit_item_id",
                "resolution",
                "reviewer_a_verdict",
                "reviewer_b_verdict",
            },
            "MathNet language-resolution item",
        )
        try:
            return cls(
                audit_item_id=value["audit_item_id"],
                reviewer_a_verdict=MathNetLanguageReviewVerdictV3(
                    value["reviewer_a_verdict"]
                ),
                reviewer_b_verdict=MathNetLanguageReviewVerdictV3(
                    value["reviewer_b_verdict"]
                ),
                resolution=MathNetLanguageResolutionCodeV3(value["resolution"]),
            )
        except (TypeError, ValueError) as exc:
            raise ValueError("serialized MathNet language-resolution item is invalid") from exc


def _resolution_code_v3(
    reviewer_a: MathNetLanguageReviewVerdictV3,
    reviewer_b: MathNetLanguageReviewVerdictV3,
) -> MathNetLanguageResolutionCodeV3:
    if type(reviewer_a) is not MathNetLanguageReviewVerdictV3 or type(
        reviewer_b
    ) is not MathNetLanguageReviewVerdictV3:
        raise ValueError("language-resolution inputs must be exact verdicts")
    if MathNetLanguageReviewVerdictV3.UNCERTAIN in {reviewer_a, reviewer_b}:
        return MathNetLanguageResolutionCodeV3.UNCERTAIN
    if reviewer_a is reviewer_b is MathNetLanguageReviewVerdictV3.ENGLISH:
        return MathNetLanguageResolutionCodeV3.AGREED_ENGLISH
    if reviewer_a is reviewer_b is MathNetLanguageReviewVerdictV3.NON_ENGLISH:
        return MathNetLanguageResolutionCodeV3.AGREED_NON_ENGLISH
    return MathNetLanguageResolutionCodeV3.DISAGREEMENT


def _report_fixed_payload_v3() -> dict[str, object]:
    return {
        "candidate_projection_authorized": False,
        "engineering_pilot_only": True,
        "external_anchor_verified_in_artifact": False,
        "independent_peer_review_verified": False,
        "local_source_replay_required_for_use": True,
        "private_gold_present": False,
        "runtime_isolation_verified_in_artifact": False,
        "schema": MATHNET_LANGUAGE_REVIEW_REPORT_SCHEMA_V3,
        "schema_version": MATHNET_LANGUAGE_REVIEW_VERSION_V3,
        "selector_handoff_authorized": False,
        "structured_source_metadata_present": False,
        "wrapper_self_authenticating": False,
    }


@dataclass(frozen=True, slots=True)
class MathNetLanguageReviewResolutionReportV3:
    projection_content_digest: str
    reviewer_payload_digest: str
    reviewer_a_manifest_digest: str
    reviewer_b_manifest_digest: str
    reviewer_a_authority_commitment: str
    reviewer_b_authority_commitment: str
    items: tuple[MathNetLanguageReviewResolutionItemV3, ...] = field(repr=False)
    agreed_english_count: int
    agreed_non_english_count: int
    uncertain_count: int
    disagreement_count: int
    content_digest: str

    def __post_init__(self) -> None:
        if type(self) is not MathNetLanguageReviewResolutionReportV3:
            raise ValueError("MathNet language-resolution report subclasses are forbidden")
        self._validate_fields()

    @property
    def item_count(self) -> int:
        return len(self.items)

    @property
    def unresolved_count(self) -> int:
        return self.uncertain_count + self.disagreement_count

    def _validate_fields(self) -> None:
        for label, value in (
            ("projection digest", self.projection_content_digest),
            ("reviewer payload digest", self.reviewer_payload_digest),
            ("reviewer-A manifest digest", self.reviewer_a_manifest_digest),
            ("reviewer-B manifest digest", self.reviewer_b_manifest_digest),
            ("reviewer-A authority commitment", self.reviewer_a_authority_commitment),
            ("reviewer-B authority commitment", self.reviewer_b_authority_commitment),
            ("report content digest", self.content_digest),
        ):
            _validate_digest(value, f"MathNet language-resolution {label}")
        if type(self.items) is not tuple or not 1 <= len(self.items) <= (
            MATHNET_LANGUAGE_AUDIT_MAX_ITEMS_V3
        ):
            raise ValueError("MathNet language-resolution items must be a bounded tuple")
        if any(type(item) is not MathNetLanguageReviewResolutionItemV3 for item in self.items):
            raise ValueError("MathNet language-resolution items must be exact")
        for item in self.items:
            item.__post_init__()
        ids = tuple(item.audit_item_id for item in self.items)
        if ids != tuple(sorted(ids)) or len(set(ids)) != len(ids):
            raise ValueError("MathNet language-resolution item ids must be unique and sorted")
        counts = {
            code: sum(item.resolution is code for item in self.items)
            for code in MathNetLanguageResolutionCodeV3
        }
        supplied_counts = {
            MathNetLanguageResolutionCodeV3.AGREED_ENGLISH: self.agreed_english_count,
            MathNetLanguageResolutionCodeV3.AGREED_NON_ENGLISH: (
                self.agreed_non_english_count
            ),
            MathNetLanguageResolutionCodeV3.UNCERTAIN: self.uncertain_count,
            MathNetLanguageResolutionCodeV3.DISAGREEMENT: self.disagreement_count,
        }
        if any(type(value) is not int or value < 0 for value in supplied_counts.values()):
            raise ValueError("MathNet language-resolution counts must be exact nonnegative ints")
        if supplied_counts != counts:
            raise ValueError("MathNet language-resolution counts differ from the items")
        expected_content = _digest(
            self._payload_without_digest(),
            domain=_REPORT_CONTENT_DOMAIN,
        )
        if not hmac.compare_digest(expected_content, self.content_digest):
            raise ValueError("MathNet language-resolution report digest is stale")

    def _payload_without_digest(self) -> dict[str, object]:
        return _report_fixed_payload_v3() | {
            "agreed_english_count": self.agreed_english_count,
            "agreed_non_english_count": self.agreed_non_english_count,
            "disagreement_count": self.disagreement_count,
            "item_count": self.item_count,
            "items": [item.to_dict() for item in self.items],
            "projection_content_digest": self.projection_content_digest,
            "reviewer_a_authority_commitment": self.reviewer_a_authority_commitment,
            "reviewer_a_manifest_digest": self.reviewer_a_manifest_digest,
            "reviewer_b_authority_commitment": self.reviewer_b_authority_commitment,
            "reviewer_b_manifest_digest": self.reviewer_b_manifest_digest,
            "reviewer_payload_digest": self.reviewer_payload_digest,
            "uncertain_count": self.uncertain_count,
            "unresolved_count": self.unresolved_count,
        }

    def validate_current(self) -> None:
        self._validate_fields()

    def to_dict(self) -> dict[str, object]:
        self.validate_current()
        return self._payload_without_digest() | {"content_digest": self.content_digest}

    def canonical_json(self) -> str:
        encoded = canonical_json(self.to_dict())
        if len(encoded.encode("utf-8")) > MATHNET_LANGUAGE_REVIEW_MAX_JSON_BYTES_V3:
            raise ValueError("MathNet language-resolution report exceeds its byte budget")
        return encoded

    @classmethod
    def from_dict(cls, raw: object) -> MathNetLanguageReviewResolutionReportV3:
        value = _mapping(raw, "MathNet language-resolution report")
        fixed = _report_fixed_payload_v3()
        expected = set(fixed) | {
            "agreed_english_count",
            "agreed_non_english_count",
            "content_digest",
            "disagreement_count",
            "item_count",
            "items",
            "projection_content_digest",
            "reviewer_a_authority_commitment",
            "reviewer_a_manifest_digest",
            "reviewer_b_authority_commitment",
            "reviewer_b_manifest_digest",
            "reviewer_payload_digest",
            "uncertain_count",
            "unresolved_count",
        }
        _exact_keys(value, expected, "MathNet language-resolution report")
        for key, fixed_value in fixed.items():
            if value[key] != fixed_value or type(value[key]) is not type(fixed_value):
                raise ValueError(f"MathNet language-resolution fixed field {key} differs")
        raw_items = value["items"]
        if type(raw_items) is not list:
            raise ValueError("serialized MathNet language-resolution items must be an array")
        items = tuple(
            MathNetLanguageReviewResolutionItemV3.from_dict(item) for item in raw_items
        )
        item_count = _exact_integer(
            value["item_count"],
            "serialized MathNet language-resolution item count",
            minimum=1,
            maximum=MATHNET_LANGUAGE_AUDIT_MAX_ITEMS_V3,
        )
        if item_count != len(items):
            raise ValueError("serialized MathNet language-resolution item count differs")
        counts = {
            key: _exact_integer(
                value[key],
                f"serialized MathNet language-resolution {key}",
                minimum=0,
                maximum=item_count,
            )
            for key in (
                "agreed_english_count",
                "agreed_non_english_count",
                "uncertain_count",
                "disagreement_count",
            )
        }
        unresolved = _exact_integer(
            value["unresolved_count"],
            "serialized MathNet language-resolution unresolved count",
            minimum=0,
            maximum=item_count,
        )
        if unresolved != counts["uncertain_count"] + counts["disagreement_count"]:
            raise ValueError("serialized MathNet language-resolution unresolved count differs")
        return cls(
            projection_content_digest=value["projection_content_digest"],
            reviewer_payload_digest=value["reviewer_payload_digest"],
            reviewer_a_manifest_digest=value["reviewer_a_manifest_digest"],
            reviewer_b_manifest_digest=value["reviewer_b_manifest_digest"],
            reviewer_a_authority_commitment=value["reviewer_a_authority_commitment"],
            reviewer_b_authority_commitment=value["reviewer_b_authority_commitment"],
            items=items,
            agreed_english_count=counts["agreed_english_count"],
            agreed_non_english_count=counts["agreed_non_english_count"],
            uncertain_count=counts["uncertain_count"],
            disagreement_count=counts["disagreement_count"],
            content_digest=value["content_digest"],
        )

    @classmethod
    def from_json(cls, raw: object) -> MathNetLanguageReviewResolutionReportV3:
        return cls.from_dict(
            _load_canonical_text(
                raw,
                "MathNet language-resolution report",
                maximum=MATHNET_LANGUAGE_REVIEW_MAX_JSON_BYTES_V3,
            )
        )

    def __reduce_ex__(self, protocol: int) -> object:
        del protocol
        raise TypeError("MathNet language-resolution reports require canonical JSON")


def _verify_manifest_v3(
    manifest: SignedMathNetLanguageReviewManifestV3,
    projection: UnsignedMathNetAuditProjectionV3,
    *,
    expected_role: MathNetLanguageReviewerRoleV3,
    verifier: MathNetReviewerAVerifierV3 | MathNetReviewerBVerifierV3,
    expected_authority_commitment: str,
    expected_key_fingerprint: str,
) -> None:
    _preflight_manifest_authority_v3(
        manifest,
        expected_role=expected_role,
        verifier=verifier,
        expected_authority_commitment=expected_authority_commitment,
        expected_key_fingerprint=expected_key_fingerprint,
    )
    for label, observed, expected in (
        ("projection digest", manifest.projection_content_digest, projection.content_digest),
        (
            "reviewer payload digest",
            manifest.reviewer_payload_digest,
            _reviewer_payload_digest_v3(projection),
        ),
        (
            "pseudonym-key commitment",
            manifest.pseudonym_key_commitment,
            projection.pseudonym_key_commitment,
        ),
    ):
        if not hmac.compare_digest(observed, expected):
            raise ValueError(f"signed MathNet language-review {label} differs")
    expected_ids = tuple(item.audit_item_id for item in projection.items)
    manifest_ids = tuple(item.audit_item_id for item in manifest.decisions)
    if manifest_ids != expected_ids:
        raise ValueError("signed MathNet language-review coverage differs from projection")


def _preflight_manifest_authority_v3(
    manifest: SignedMathNetLanguageReviewManifestV3,
    *,
    expected_role: MathNetLanguageReviewerRoleV3,
    verifier: MathNetReviewerAVerifierV3 | MathNetReviewerBVerifierV3,
    expected_authority_commitment: str,
    expected_key_fingerprint: str,
) -> None:
    """Reject wrong roles, keys, and signatures before any source-byte replay."""

    if type(manifest) is not SignedMathNetLanguageReviewManifestV3:
        raise ValueError("language-review verification requires an exact signed manifest")
    manifest.validate_current()
    if manifest.reviewer_role is not expected_role:
        raise ValueError("signed MathNet language-review manifest has the wrong role")
    if expected_role is MathNetLanguageReviewerRoleV3.REVIEWER_A:
        if type(verifier) is not MathNetReviewerAVerifierV3:
            raise ValueError("reviewer-A manifest requires the reviewer-A verifier")
    elif type(verifier) is not MathNetReviewerBVerifierV3:
        raise ValueError("reviewer-B manifest requires the reviewer-B verifier")
    expected_commitment = _validate_digest(
        expected_authority_commitment,
        "expected MathNet reviewer authority commitment",
    )
    expected_fingerprint = _validate_digest(
        expected_key_fingerprint,
        "expected MathNet reviewer key fingerprint",
    )
    for label, observed, expected in (
        (
            "authority commitment",
            manifest.reviewer_authority_commitment,
            expected_commitment,
        ),
        ("verifier commitment", verifier.authority_commitment, expected_commitment),
        ("key fingerprint", manifest.reviewer_key_fingerprint, expected_fingerprint),
        ("verifier fingerprint", verifier.key_fingerprint, expected_fingerprint),
    ):
        if not hmac.compare_digest(observed, expected):
            raise ValueError(f"signed MathNet language-review {label} differs")
    verifier._verify(manifest)


def verify_and_resolve_mathnet_language_reviews_v3(
    source: PreparedPinnedMathNetSourceV3,
    prepared_universe: PreparedMathNetPilotUniverseV3,
    evaluator_vault: PreparedMathNetPilotEvaluatorVaultV3,
    reviewer_a_manifest: SignedMathNetLanguageReviewManifestV3,
    reviewer_b_manifest: SignedMathNetLanguageReviewManifestV3,
    *,
    vault_key: bytes,
    pseudonym_key: bytes,
    reviewer_a_verifier: MathNetReviewerAVerifierV3,
    reviewer_b_verifier: MathNetReviewerBVerifierV3,
    expected_a_authority_commitment: str,
    expected_b_authority_commitment: str,
    expected_a_key_fingerprint: str,
    expected_b_key_fingerprint: str,
    batch_size: int = 512,
) -> MathNetLanguageReviewResolutionReportV3:
    """Replay, verify externally expected keys, and emit a non-authoritative report."""

    if type(reviewer_a_verifier) is not MathNetReviewerAVerifierV3 or type(
        reviewer_b_verifier
    ) is not MathNetReviewerBVerifierV3:
        raise ValueError("language-review resolution requires exact role verifiers")
    if hmac.compare_digest(
        reviewer_a_verifier.key_fingerprint,
        reviewer_b_verifier.key_fingerprint,
    ):
        raise ValueError("language reviewers reuse the same underlying Ed25519 key")
    if type(reviewer_a_manifest) is not SignedMathNetLanguageReviewManifestV3 or type(
        reviewer_b_manifest
    ) is not SignedMathNetLanguageReviewManifestV3:
        raise ValueError("language-review resolution requires exact signed manifests")
    reviewer_a_manifest.validate_current()
    reviewer_b_manifest.validate_current()
    if hmac.compare_digest(
        reviewer_a_manifest.review_runtime_evidence_digest,
        reviewer_b_manifest.review_runtime_evidence_digest,
    ):
        raise ValueError("language reviewers reuse the same runtime evidence")
    _preflight_manifest_authority_v3(
        reviewer_a_manifest,
        expected_role=MathNetLanguageReviewerRoleV3.REVIEWER_A,
        verifier=reviewer_a_verifier,
        expected_authority_commitment=expected_a_authority_commitment,
        expected_key_fingerprint=expected_a_key_fingerprint,
    )
    _preflight_manifest_authority_v3(
        reviewer_b_manifest,
        expected_role=MathNetLanguageReviewerRoleV3.REVIEWER_B,
        verifier=reviewer_b_verifier,
        expected_authority_commitment=expected_b_authority_commitment,
        expected_key_fingerprint=expected_b_key_fingerprint,
    )

    for label, observed, expected in (
        (
            "projection content digest",
            reviewer_b_manifest.projection_content_digest,
            reviewer_a_manifest.projection_content_digest,
        ),
        (
            "reviewer payload digest",
            reviewer_b_manifest.reviewer_payload_digest,
            reviewer_a_manifest.reviewer_payload_digest,
        ),
        (
            "pseudonym-key commitment",
            reviewer_b_manifest.pseudonym_key_commitment,
            reviewer_a_manifest.pseudonym_key_commitment,
        ),
    ):
        if not hmac.compare_digest(observed, expected):
            raise ValueError(f"signed MathNet language-review pair {label} differs")
    reviewer_a_ids = tuple(item.audit_item_id for item in reviewer_a_manifest.decisions)
    reviewer_b_ids = tuple(item.audit_item_id for item in reviewer_b_manifest.decisions)
    if reviewer_a_ids != reviewer_b_ids:
        raise ValueError("signed MathNet language-review pair decision coverage differs")
    if type(pseudonym_key) is not bytes or not 32 <= len(pseudonym_key) <= 64:
        raise ValueError("MathNet language-review pseudonym key must contain 32 to 64 bytes")
    supplied_pseudonym_commitment = _pseudonym_key_commitment_v3(pseudonym_key)
    if not hmac.compare_digest(
        supplied_pseudonym_commitment,
        reviewer_a_manifest.pseudonym_key_commitment,
    ):
        raise ValueError("signed MathNet language-review pseudonym key commitment differs")

    projection = build_unsigned_mathnet_language_audit_projection_v3(
        source,
        prepared_universe,
        evaluator_vault,
        vault_key=vault_key,
        pseudonym_key=pseudonym_key,
        batch_size=batch_size,
    )
    _verify_manifest_v3(
        reviewer_a_manifest,
        projection,
        expected_role=MathNetLanguageReviewerRoleV3.REVIEWER_A,
        verifier=reviewer_a_verifier,
        expected_authority_commitment=expected_a_authority_commitment,
        expected_key_fingerprint=expected_a_key_fingerprint,
    )
    _verify_manifest_v3(
        reviewer_b_manifest,
        projection,
        expected_role=MathNetLanguageReviewerRoleV3.REVIEWER_B,
        verifier=reviewer_b_verifier,
        expected_authority_commitment=expected_b_authority_commitment,
        expected_key_fingerprint=expected_b_key_fingerprint,
    )

    items = tuple(
        MathNetLanguageReviewResolutionItemV3(
            audit_item_id=left.audit_item_id,
            reviewer_a_verdict=left.verdict,
            reviewer_b_verdict=right.verdict,
            resolution=_resolution_code_v3(left.verdict, right.verdict),
        )
        for left, right in zip(
            reviewer_a_manifest.decisions,
            reviewer_b_manifest.decisions,
            strict=True,
        )
    )
    counts = {
        code: sum(item.resolution is code for item in items)
        for code in MathNetLanguageResolutionCodeV3
    }
    report_payload = _report_fixed_payload_v3() | {
        "agreed_english_count": counts[
            MathNetLanguageResolutionCodeV3.AGREED_ENGLISH
        ],
        "agreed_non_english_count": counts[
            MathNetLanguageResolutionCodeV3.AGREED_NON_ENGLISH
        ],
        "disagreement_count": counts[MathNetLanguageResolutionCodeV3.DISAGREEMENT],
        "item_count": len(items),
        "items": [item.to_dict() for item in items],
        "projection_content_digest": projection.content_digest,
        "reviewer_a_authority_commitment": reviewer_a_manifest.reviewer_authority_commitment,
        "reviewer_a_manifest_digest": reviewer_a_manifest.content_digest,
        "reviewer_b_authority_commitment": reviewer_b_manifest.reviewer_authority_commitment,
        "reviewer_b_manifest_digest": reviewer_b_manifest.content_digest,
        "reviewer_payload_digest": _reviewer_payload_digest_v3(projection),
        "uncertain_count": counts[MathNetLanguageResolutionCodeV3.UNCERTAIN],
        "unresolved_count": (
            counts[MathNetLanguageResolutionCodeV3.UNCERTAIN]
            + counts[MathNetLanguageResolutionCodeV3.DISAGREEMENT]
        ),
    }
    report = MathNetLanguageReviewResolutionReportV3(
        projection_content_digest=projection.content_digest,
        reviewer_payload_digest=_reviewer_payload_digest_v3(projection),
        reviewer_a_manifest_digest=reviewer_a_manifest.content_digest,
        reviewer_b_manifest_digest=reviewer_b_manifest.content_digest,
        reviewer_a_authority_commitment=reviewer_a_manifest.reviewer_authority_commitment,
        reviewer_b_authority_commitment=reviewer_b_manifest.reviewer_authority_commitment,
        items=items,
        agreed_english_count=counts[MathNetLanguageResolutionCodeV3.AGREED_ENGLISH],
        agreed_non_english_count=counts[
            MathNetLanguageResolutionCodeV3.AGREED_NON_ENGLISH
        ],
        uncertain_count=counts[MathNetLanguageResolutionCodeV3.UNCERTAIN],
        disagreement_count=counts[MathNetLanguageResolutionCodeV3.DISAGREEMENT],
        content_digest=_digest(report_payload, domain=_REPORT_CONTENT_DOMAIN),
    )
    report.validate_current()
    return report


__all__ = [
    "MATHNET_LANGUAGE_REVIEW_MANIFEST_SCHEMA_V3",
    "MATHNET_LANGUAGE_REVIEW_MAX_JSON_BYTES_V3",
    "MATHNET_LANGUAGE_REVIEW_MAX_STDOUT_BYTES_V3",
    "MATHNET_LANGUAGE_REVIEW_OUTPUT_SCHEMA_V3",
    "MATHNET_LANGUAGE_REVIEW_REPORT_SCHEMA_V3",
    "MATHNET_LANGUAGE_REVIEW_SCHEMA_DIALECT_V3",
    "MATHNET_LANGUAGE_REVIEW_VERSION_V3",
    "MATHNET_REVIEWER_A_DOMAIN_V3",
    "MATHNET_REVIEWER_B_DOMAIN_V3",
    "MathNetLanguageResolutionCodeV3",
    "MathNetLanguageReviewDecisionV3",
    "MathNetLanguageReviewResolutionItemV3",
    "MathNetLanguageReviewResolutionReportV3",
    "MathNetLanguageReviewSubmissionV3",
    "MathNetLanguageReviewVerdictV3",
    "MathNetLanguageReviewerRoleV3",
    "MathNetReviewerASignerV3",
    "MathNetReviewerAVerifierV3",
    "MathNetReviewerBSignerV3",
    "MathNetReviewerBVerifierV3",
    "SignedMathNetLanguageReviewManifestV3",
    "mathnet_language_review_output_schema_v3",
    "mathnet_language_review_stdin_v3",
    "parse_mathnet_language_review_stdout_v3",
    "sign_replayed_mathnet_language_review_manifest_v3",
    "verify_and_resolve_mathnet_language_reviews_v3",
]
