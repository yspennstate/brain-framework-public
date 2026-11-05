"""Offline tokenizer attestation and exact final-prompt padding for pilot v3.

The module deliberately ships no model-to-tokenizer guess and performs no network access.
Callers bind two independently identified offline encoders to a committed manifest.  Every
receipt is then regenerated from the exact text by both encoders; caller-supplied counts are
never accepted as evidence.

``engineering_proxy`` is an explicit descriptive status.  A strict served-runtime claim also
requires a signature-valid response-side served-model/tokenizer binding from a precommitted
authority.  The current desktop runtime has no such evidence, so its suite must remain a proxy.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from .canonical_ed25519_v3 import CanonicalEd25519VerifierV3
from .models import canonical_json
from .three_arm_evaluation import TokenizerReceipt

TOKENIZER_ATTESTATION_SCHEMA_V3 = "org.math-research-brain/offline-tokenizer-attestation/v3"
SERVED_TOKENIZER_ATTESTATION_SCHEMA_V3 = "org.math-research-brain/served-tokenizer-binding/v3"
SERVED_TOKENIZER_ATTESTATION_DOMAIN_V3 = "org.math-research-brain/served-tokenizer-binding/v3"
EXACT_PROMPT_PADDING_SCHEMA_V3 = "org.math-research-brain/exact-prompt-padding/v3"
RAW_WHITESPACE_PADDING_SLOT_V3 = "{{RAW_WHITESPACE_PADDING_V3}}"
DEFAULT_PADDING_PATTERNS_V3 = tuple(sorted({" ", "\t", "\n", " \t", " \n", "\t\n", " \t\n"}))

_DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}\Z", re.ASCII)
_SIGNATURE_RE = re.compile(r"ed25519:[0-9a-f]{128}\Z", re.ASCII)
_IDENTIFIER_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/+-]{0,255}\Z", re.ASCII)
_TOKEN_ID_DOMAIN = b"math-brain/canonical-token-ids/v3\x00"
_MAX_TEXT_BYTES = 1_000_000
_MAX_TOKEN_COUNT = 2_000_000
_MAX_PADDING_BYTES = 16_384
_MAX_PADDING_CANDIDATES = 100_000
_VERIFIED_SUITE_SEAL = object()


class TokenizerAttestationError(ValueError):
    """Raised when tokenizer evidence cannot be independently reconstructed."""


class PromptPaddingInfeasibleError(TokenizerAttestationError):
    """Raised when the precommitted finite padding search has no exact solution."""


class TokenizerEvidenceClassV3(StrEnum):
    SERVED_ATTESTED = "served_attested"
    ENGINEERING_PROXY = "engineering_proxy"


class TokenMeasurementScopeV3(StrEnum):
    PACKET_TEXT = "packet_text"
    FINAL_SOLVER_PROMPT = "final_solver_prompt"


def _sha256_bytes(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _digest_json(value: Any) -> str:
    return _sha256_bytes(canonical_json(value).encode("utf-8"))


def _digest(value: object, label: str) -> str:
    if type(value) is not str or _DIGEST_RE.fullmatch(value) is None:
        raise TokenizerAttestationError(f"{label} must be canonical sha256:<lowercase hex>")
    return value


def _identifier(value: object, label: str) -> str:
    if type(value) is not str or _IDENTIFIER_RE.fullmatch(value) is None:
        raise TokenizerAttestationError(f"{label} must be bounded ASCII identifier text")
    return value


def _utf8_text(
    value: object,
    label: str,
    *,
    allow_empty: bool = False,
    maximum_bytes: int = _MAX_TEXT_BYTES,
) -> str:
    if type(value) is not str:
        raise TokenizerAttestationError(f"{label} must be exact text")
    try:
        encoded = value.encode("utf-8", errors="strict")
    except UnicodeEncodeError as error:
        raise TokenizerAttestationError(f"{label} must be valid UTF-8") from error
    if not allow_empty and not value:
        raise TokenizerAttestationError(f"{label} must not be empty")
    if len(encoded) > maximum_bytes:
        raise TokenizerAttestationError(f"{label} exceeds its byte budget")
    return value


def _canonical_bytes(value: dict[str, Any]) -> bytes:
    return canonical_json(value).encode("utf-8")


def _u64(value: object, label: str) -> int:
    if type(value) is not int or not 0 <= value < 2**64:
        raise TokenizerAttestationError(f"{label} must be an unsigned 64-bit exact integer")
    return value


def _length_prefixed(raw: bytes) -> bytes:
    return len(raw).to_bytes(8, "big") + raw


def canonical_token_ids_digest_v3(
    token_ids: Sequence[int],
    *,
    tokenizer_spec_digest: str,
    text_digest: str,
    scope: TokenMeasurementScopeV3,
    framing_digest: str,
) -> str:
    """Digest one exact token vector using unambiguous canonical unsigned-64 framing."""

    if type(token_ids) not in {list, tuple}:
        raise TokenizerAttestationError("token ids must be a bounded list or tuple")
    materialized = tuple(token_ids)
    if not 1 <= len(materialized) <= _MAX_TOKEN_COUNT:
        raise TokenizerAttestationError("token vector exceeds its cardinality bounds")
    if type(scope) is not TokenMeasurementScopeV3:
        raise TokenizerAttestationError("token measurement scope must be exact")
    _digest(tokenizer_spec_digest, "tokenizer specification digest")
    _digest(text_digest, "tokenized text digest")
    _digest(framing_digest, "tokenization framing digest")
    validated_ids = tuple(_u64(item, "token id") for item in materialized)
    payload = bytearray(_TOKEN_ID_DOMAIN)
    for value in (
        tokenizer_spec_digest.encode("ascii"),
        text_digest.encode("ascii"),
        scope.value.encode("ascii"),
        framing_digest.encode("ascii"),
    ):
        payload.extend(_length_prefixed(value))
    payload.extend(len(validated_ids).to_bytes(8, "big"))
    for token_id in validated_ids:
        payload.extend(token_id.to_bytes(8, "big"))
    return _sha256_bytes(bytes(payload))


@dataclass(frozen=True, slots=True)
class TokenizerSpecV3:
    tokenizer_id: str
    encoding_name: str
    vocabulary_artifact_digest: str
    encode_options_digest: str
    primary_implementation_id: str
    primary_implementation_artifact_digest: str
    verifier_implementation_id: str
    verifier_implementation_artifact_digest: str
    normalization: str = "none_utf8"
    encode_mode: str = "ordinary_no_special_tokens"

    def __post_init__(self) -> None:
        if type(self) is not TokenizerSpecV3:
            raise TokenizerAttestationError("tokenizer specification subclasses are forbidden")
        for label, value in (
            ("tokenizer id", self.tokenizer_id),
            ("encoding name", self.encoding_name),
            ("primary implementation id", self.primary_implementation_id),
            ("verifier implementation id", self.verifier_implementation_id),
        ):
            _identifier(value, label)
        for label, value in (
            ("vocabulary artifact digest", self.vocabulary_artifact_digest),
            ("encode-options digest", self.encode_options_digest),
            ("primary implementation artifact digest", self.primary_implementation_artifact_digest),
            (
                "verifier implementation artifact digest",
                self.verifier_implementation_artifact_digest,
            ),
        ):
            _digest(value, label)
        if self.primary_implementation_id == self.verifier_implementation_id:
            raise TokenizerAttestationError("primary and verifier implementations must be distinct")
        if (
            self.primary_implementation_artifact_digest
            == self.verifier_implementation_artifact_digest
        ):
            raise TokenizerAttestationError(
                "primary and verifier implementation artifacts must be distinct"
            )
        if self.normalization != "none_utf8":
            raise TokenizerAttestationError("tokenizer normalization must be none_utf8")
        if self.encode_mode != "ordinary_no_special_tokens":
            raise TokenizerAttestationError(
                "tokenizer encode mode must forbid special-token interpretation"
            )

    def commitment_record(self) -> dict[str, Any]:
        self.__post_init__()
        return {
            "encode_mode": self.encode_mode,
            "encode_options_digest": self.encode_options_digest,
            "encoding_name": self.encoding_name,
            "normalization": self.normalization,
            "primary_implementation_artifact_digest": (self.primary_implementation_artifact_digest),
            "primary_implementation_id": self.primary_implementation_id,
            "tokenizer_id": self.tokenizer_id,
            "verifier_implementation_artifact_digest": (
                self.verifier_implementation_artifact_digest
            ),
            "verifier_implementation_id": self.verifier_implementation_id,
            "vocabulary_artifact_digest": self.vocabulary_artifact_digest,
        }

    @property
    def digest(self) -> str:
        return _digest_json(self.commitment_record())


@dataclass(frozen=True, slots=True)
class TokenizerSuiteManifestV3:
    requested_model_selector: str
    evidence_class: TokenizerEvidenceClassV3
    tokenizers: tuple[TokenizerSpecV3, ...]
    engineering_proxy_basis_digest: str | None = None
    served_attestation_authority_commitment: str | None = None

    def __post_init__(self) -> None:
        if type(self) is not TokenizerSuiteManifestV3:
            raise TokenizerAttestationError("tokenizer suite subclasses are forbidden")
        _identifier(self.requested_model_selector, "requested model selector")
        if type(self.evidence_class) is not TokenizerEvidenceClassV3:
            raise TokenizerAttestationError("tokenizer evidence class must be explicit")
        if type(self.tokenizers) is not tuple or not self.tokenizers:
            raise TokenizerAttestationError("tokenizer suite must be a nonempty tuple")
        if any(type(spec) is not TokenizerSpecV3 for spec in self.tokenizers):
            raise TokenizerAttestationError("tokenizer suite requires exact specifications")
        for spec in self.tokenizers:
            spec.__post_init__()
        identities = tuple(spec.tokenizer_id for spec in self.tokenizers)
        if tuple(sorted(set(identities))) != identities:
            raise TokenizerAttestationError("tokenizer specifications must be unique and sorted")
        if self.evidence_class is TokenizerEvidenceClassV3.ENGINEERING_PROXY:
            if self.engineering_proxy_basis_digest is None:
                raise TokenizerAttestationError(
                    "engineering_proxy requires an explicit proxy-basis digest"
                )
            _digest(self.engineering_proxy_basis_digest, "engineering proxy basis digest")
            if self.served_attestation_authority_commitment is not None:
                raise TokenizerAttestationError(
                    "engineering_proxy cannot imply a served-attestation authority"
                )
        else:
            if self.engineering_proxy_basis_digest is not None:
                raise TokenizerAttestationError(
                    "served_attested cannot carry an engineering proxy basis"
                )
            if self.served_attestation_authority_commitment is None:
                raise TokenizerAttestationError(
                    "served_attested requires a precommitted attestation authority"
                )
            _digest(
                self.served_attestation_authority_commitment,
                "served attestation authority commitment",
            )

    def commitment_record(self) -> dict[str, Any]:
        self.__post_init__()
        return {
            "engineering_proxy_basis_digest": self.engineering_proxy_basis_digest,
            "evidence_class": self.evidence_class.value,
            "record_type": TOKENIZER_ATTESTATION_SCHEMA_V3,
            "requested_model_selector": self.requested_model_selector,
            "schema_version": 3,
            "served_attestation_authority_commitment": (
                self.served_attestation_authority_commitment
            ),
            "tokenizers": [spec.commitment_record() for spec in self.tokenizers],
        }

    @property
    def digest(self) -> str:
        return _digest_json(self.commitment_record())

    def public_commitment(self) -> dict[str, Any]:
        return {
            "evidence_class": self.evidence_class.value,
            "record_type": TOKENIZER_ATTESTATION_SCHEMA_V3,
            "requested_model_selector": self.requested_model_selector,
            "schema_version": 3,
            "suite_manifest_digest": self.digest,
            "tokenizer_count": len(self.tokenizers),
        }


def served_tokenizer_attestation_unsigned_record_v3(
    *,
    requested_model_selector: str,
    observed_served_model: str,
    tokenizer_spec_digests: tuple[str, ...],
    response_model_identity_evidence_digest: str,
    model_tokenizer_mapping_evidence_digest: str,
    authority_commitment: str,
) -> dict[str, Any]:
    _identifier(requested_model_selector, "attested requested model selector")
    _identifier(observed_served_model, "observed served model")
    if type(tokenizer_spec_digests) is not tuple or not tokenizer_spec_digests:
        raise TokenizerAttestationError("attestation requires tokenizer specification digests")
    for value in tokenizer_spec_digests:
        _digest(value, "attested tokenizer specification digest")
    if tuple(sorted(set(tokenizer_spec_digests))) != tokenizer_spec_digests:
        raise TokenizerAttestationError(
            "attested tokenizer specification digests must be unique and sorted"
        )
    _digest(
        response_model_identity_evidence_digest,
        "response-side model identity evidence digest",
    )
    _digest(
        model_tokenizer_mapping_evidence_digest,
        "model-tokenizer mapping evidence digest",
    )
    _digest(authority_commitment, "served-tokenizer authority commitment")
    return {
        "authority_commitment": authority_commitment,
        "model_tokenizer_mapping_evidence_digest": (model_tokenizer_mapping_evidence_digest),
        "observed_served_model": observed_served_model,
        "record_type": SERVED_TOKENIZER_ATTESTATION_SCHEMA_V3,
        "requested_model_selector": requested_model_selector,
        "response_model_identity_evidence_digest": (response_model_identity_evidence_digest),
        "schema_version": 3,
        "tokenizer_spec_digests": list(tokenizer_spec_digests),
    }


@dataclass(frozen=True, slots=True)
class ServedTokenizerAttestationV3:
    requested_model_selector: str
    observed_served_model: str
    tokenizer_spec_digests: tuple[str, ...]
    response_model_identity_evidence_digest: str
    model_tokenizer_mapping_evidence_digest: str
    authority_commitment: str
    authority_signature: str

    def __post_init__(self) -> None:
        if type(self) is not ServedTokenizerAttestationV3:
            raise TokenizerAttestationError("served-tokenizer attestation subclasses are forbidden")
        self.unsigned_record()
        if (
            type(self.authority_signature) is not str
            or _SIGNATURE_RE.fullmatch(self.authority_signature) is None
        ):
            raise TokenizerAttestationError(
                "served-tokenizer authority signature must be canonical Ed25519 hex"
            )

    def unsigned_record(self) -> dict[str, Any]:
        return served_tokenizer_attestation_unsigned_record_v3(
            requested_model_selector=self.requested_model_selector,
            observed_served_model=self.observed_served_model,
            tokenizer_spec_digests=self.tokenizer_spec_digests,
            response_model_identity_evidence_digest=(self.response_model_identity_evidence_digest),
            model_tokenizer_mapping_evidence_digest=(self.model_tokenizer_mapping_evidence_digest),
            authority_commitment=self.authority_commitment,
        )

    @property
    def digest(self) -> str:
        return _digest_json(
            {
                "authority_signature": self.authority_signature,
                "unsigned_record": self.unsigned_record(),
            }
        )


@dataclass(frozen=True, slots=True)
class OfflineTokenizerBackendV3:
    tokenizer_id: str
    implementation_id: str
    implementation_artifact_digest: str
    vocabulary_artifact_digest: str
    encode_options_digest: str
    encoder: Callable[[str], Sequence[int]] = field(repr=False, compare=False)

    def __post_init__(self) -> None:
        if type(self) is not OfflineTokenizerBackendV3:
            raise TokenizerAttestationError("offline tokenizer backend subclasses are forbidden")
        _identifier(self.tokenizer_id, "backend tokenizer id")
        _identifier(self.implementation_id, "backend implementation id")
        for label, value in (
            ("backend implementation artifact digest", self.implementation_artifact_digest),
            ("backend vocabulary artifact digest", self.vocabulary_artifact_digest),
            ("backend encode-options digest", self.encode_options_digest),
        ):
            _digest(value, label)
        if not callable(self.encoder):
            raise TokenizerAttestationError("backend encoder must be callable")

    def encode_ordinary(self, text: str) -> tuple[int, ...]:
        _utf8_text(text, "tokenizer input")
        try:
            raw = self.encoder(text)
        except Exception as error:
            raise TokenizerAttestationError("offline tokenizer backend failed") from error
        if type(raw) not in {list, tuple}:
            raise TokenizerAttestationError("offline tokenizer must return a bounded list or tuple")
        materialized = tuple(raw)
        if not 1 <= len(materialized) <= _MAX_TOKEN_COUNT:
            raise TokenizerAttestationError("offline tokenizer returned an invalid token count")
        return tuple(_u64(item, "backend token id") for item in materialized)


@dataclass(frozen=True, slots=True)
class TokenizerBackendPairV3:
    primary: OfflineTokenizerBackendV3
    verifier: OfflineTokenizerBackendV3

    def __post_init__(self) -> None:
        if type(self) is not TokenizerBackendPairV3:
            raise TokenizerAttestationError("tokenizer backend-pair subclasses are forbidden")
        if (
            type(self.primary) is not OfflineTokenizerBackendV3
            or type(self.verifier) is not OfflineTokenizerBackendV3
        ):
            raise TokenizerAttestationError("backend pair requires exact offline backends")
        self.primary.__post_init__()
        self.verifier.__post_init__()
        if self.primary.tokenizer_id != self.verifier.tokenizer_id:
            raise TokenizerAttestationError("independent backends must target one tokenizer id")
        if self.primary.implementation_id == self.verifier.implementation_id:
            raise TokenizerAttestationError("independent backend implementation ids must differ")
        if (
            self.primary.implementation_artifact_digest
            == self.verifier.implementation_artifact_digest
        ):
            raise TokenizerAttestationError("independent backend artifacts must differ")
        if self.primary.encoder is self.verifier.encoder:
            raise TokenizerAttestationError("independent backends cannot reuse one encoder object")


def _validate_backend_pair_against_spec(
    pair: TokenizerBackendPairV3, spec: TokenizerSpecV3
) -> None:
    pair.__post_init__()
    expected_primary = (
        spec.tokenizer_id,
        spec.primary_implementation_id,
        spec.primary_implementation_artifact_digest,
        spec.vocabulary_artifact_digest,
        spec.encode_options_digest,
    )
    expected_verifier = (
        spec.tokenizer_id,
        spec.verifier_implementation_id,
        spec.verifier_implementation_artifact_digest,
        spec.vocabulary_artifact_digest,
        spec.encode_options_digest,
    )
    observed_primary = (
        pair.primary.tokenizer_id,
        pair.primary.implementation_id,
        pair.primary.implementation_artifact_digest,
        pair.primary.vocabulary_artifact_digest,
        pair.primary.encode_options_digest,
    )
    observed_verifier = (
        pair.verifier.tokenizer_id,
        pair.verifier.implementation_id,
        pair.verifier.implementation_artifact_digest,
        pair.verifier.vocabulary_artifact_digest,
        pair.verifier.encode_options_digest,
    )
    if observed_primary != expected_primary or observed_verifier != expected_verifier:
        raise TokenizerAttestationError("offline backend identity differs from its manifest")


def _validate_served_attestation_against_manifest(
    manifest: TokenizerSuiteManifestV3,
    attestation: ServedTokenizerAttestationV3,
    verifier: CanonicalEd25519VerifierV3,
) -> None:
    if (
        type(attestation) is not ServedTokenizerAttestationV3
        or type(verifier) is not CanonicalEd25519VerifierV3
    ):
        raise TokenizerAttestationError(
            "served_attested requires exact signed evidence and its verifier"
        )
    attestation.__post_init__()
    expected_authority = manifest.served_attestation_authority_commitment
    if (
        verifier.authority_commitment != expected_authority
        or attestation.authority_commitment != expected_authority
    ):
        raise TokenizerAttestationError("served attestation authority differs from manifest")
    verifier.verify(
        _canonical_bytes(attestation.unsigned_record()),
        attestation.authority_signature,
    )
    if (
        attestation.requested_model_selector != manifest.requested_model_selector
        or attestation.observed_served_model != manifest.requested_model_selector
    ):
        raise TokenizerAttestationError(
            "response-side served model differs from the requested selector"
        )
    expected_spec_digests = tuple(sorted(spec.digest for spec in manifest.tokenizers))
    if attestation.tokenizer_spec_digests != expected_spec_digests:
        raise TokenizerAttestationError(
            "served model-tokenizer mapping differs from the tokenizer suite"
        )


@dataclass(frozen=True, slots=True)
class VerifiedTokenizerSuiteV3:
    manifest: TokenizerSuiteManifestV3
    backend_pairs: tuple[TokenizerBackendPairV3, ...] = field(repr=False)
    served_attestation: ServedTokenizerAttestationV3 | None = field(repr=False)
    served_attestation_authority_public_key: bytes | None = field(repr=False, compare=False)
    served_attestation_digest: str | None
    binding_digest: str
    _seal: object = field(repr=False, compare=False)

    def __post_init__(self) -> None:
        if type(self) is not VerifiedTokenizerSuiteV3:
            raise TokenizerAttestationError("verified tokenizer suite subclasses are forbidden")
        if self._seal is not _VERIFIED_SUITE_SEAL:
            raise TokenizerAttestationError("verified tokenizer suite must come from the binder")
        if type(self.manifest) is not TokenizerSuiteManifestV3:
            raise TokenizerAttestationError("verified suite requires an exact manifest")
        self.manifest.__post_init__()
        if type(self.backend_pairs) is not tuple or len(self.backend_pairs) != len(
            self.manifest.tokenizers
        ):
            raise TokenizerAttestationError("verified suite backend universe is incomplete")
        for pair, spec in zip(self.backend_pairs, self.manifest.tokenizers, strict=True):
            _validate_backend_pair_against_spec(pair, spec)
        if tuple(pair.primary.tokenizer_id for pair in self.backend_pairs) != tuple(
            spec.tokenizer_id for spec in self.manifest.tokenizers
        ):
            raise TokenizerAttestationError("verified suite backend order is not canonical")
        if self.manifest.evidence_class is TokenizerEvidenceClassV3.SERVED_ATTESTED:
            if (
                type(self.served_attestation) is not ServedTokenizerAttestationV3
                or type(self.served_attestation_authority_public_key) is not bytes
                or self.served_attestation_digest is None
            ):
                raise TokenizerAttestationError("strict suite is missing served attestation")
            verifier = CanonicalEd25519VerifierV3(
                self.served_attestation_authority_public_key,
                domain=SERVED_TOKENIZER_ATTESTATION_DOMAIN_V3,
            )
            _validate_served_attestation_against_manifest(
                self.manifest,
                self.served_attestation,
                verifier,
            )
            _digest(self.served_attestation_digest, "served attestation digest")
            if self.served_attestation_digest != self.served_attestation.digest:
                raise TokenizerAttestationError(
                    "served attestation digest differs from its signed evidence"
                )
        elif any(
            item is not None
            for item in (
                self.served_attestation,
                self.served_attestation_authority_public_key,
                self.served_attestation_digest,
            )
        ):
            raise TokenizerAttestationError("engineering proxy cannot carry served attestation")
        _digest(self.binding_digest, "tokenizer suite binding digest")
        expected = _digest_json(
            {
                "backend_pairs": [
                    {
                        "primary_artifact_digest": pair.primary.implementation_artifact_digest,
                        "primary_implementation_id": pair.primary.implementation_id,
                        "tokenizer_id": pair.primary.tokenizer_id,
                        "verifier_artifact_digest": pair.verifier.implementation_artifact_digest,
                        "verifier_implementation_id": pair.verifier.implementation_id,
                    }
                    for pair in self.backend_pairs
                ],
                "served_attestation_digest": self.served_attestation_digest,
                "suite_manifest_digest": self.manifest.digest,
            }
        )
        if self.binding_digest != expected:
            raise TokenizerAttestationError("tokenizer suite binding differs from its evidence")

    @property
    def strict_served_attested_eligible(self) -> bool:
        self.__post_init__()
        return (
            self.manifest.evidence_class is TokenizerEvidenceClassV3.SERVED_ATTESTED
            and self.served_attestation is not None
            and self.served_attestation_authority_public_key is not None
            and self.served_attestation_digest is not None
        )


def bind_tokenizer_suite_v3(
    manifest: TokenizerSuiteManifestV3,
    backend_pairs: Sequence[TokenizerBackendPairV3],
    *,
    expected_manifest_digest: str,
    served_attestation: ServedTokenizerAttestationV3 | None = None,
    served_attestation_verifier: CanonicalEd25519VerifierV3 | None = None,
) -> VerifiedTokenizerSuiteV3:
    """Bind exact offline encoders and, for strict use, verify signed served evidence."""

    if type(manifest) is not TokenizerSuiteManifestV3:
        raise TokenizerAttestationError("tokenizer suite binder requires an exact manifest")
    manifest.__post_init__()
    _digest(expected_manifest_digest, "expected tokenizer suite manifest digest")
    if manifest.digest != expected_manifest_digest:
        raise TokenizerAttestationError("tokenizer suite differs from its expected digest")
    if type(backend_pairs) not in {list, tuple}:
        raise TokenizerAttestationError("backend pairs must be a bounded sequence")
    pair_by_id: dict[str, TokenizerBackendPairV3] = {}
    for pair in tuple(backend_pairs):
        if type(pair) is not TokenizerBackendPairV3:
            raise TokenizerAttestationError("backend pairs must be exact records")
        pair.__post_init__()
        tokenizer_id = pair.primary.tokenizer_id
        if tokenizer_id in pair_by_id:
            raise TokenizerAttestationError("backend tokenizer ids must be unique")
        pair_by_id[tokenizer_id] = pair
    expected_ids = {spec.tokenizer_id for spec in manifest.tokenizers}
    if set(pair_by_id) != expected_ids:
        raise TokenizerAttestationError("backend and manifest tokenizer universes differ")
    normalized_pairs = tuple(pair_by_id[spec.tokenizer_id] for spec in manifest.tokenizers)
    for pair, spec in zip(normalized_pairs, manifest.tokenizers, strict=True):
        _validate_backend_pair_against_spec(pair, spec)

    attestation_object: ServedTokenizerAttestationV3 | None = None
    attestation_authority_public_key: bytes | None = None
    attestation_digest: str | None = None
    if manifest.evidence_class is TokenizerEvidenceClassV3.ENGINEERING_PROXY:
        if served_attestation is not None or served_attestation_verifier is not None:
            raise TokenizerAttestationError(
                "engineering_proxy must not be upgraded by incidental served evidence"
            )
    else:
        if (
            type(served_attestation) is not ServedTokenizerAttestationV3
            or type(served_attestation_verifier) is not CanonicalEd25519VerifierV3
        ):
            raise TokenizerAttestationError(
                "served_attested requires exact signed evidence and its verifier"
            )
        _validate_served_attestation_against_manifest(
            manifest,
            served_attestation,
            served_attestation_verifier,
        )
        attestation_object = served_attestation
        attestation_authority_public_key = served_attestation_verifier.public_key_bytes()
        attestation_digest = served_attestation.digest

    binding_digest = _digest_json(
        {
            "backend_pairs": [
                {
                    "primary_artifact_digest": pair.primary.implementation_artifact_digest,
                    "primary_implementation_id": pair.primary.implementation_id,
                    "tokenizer_id": pair.primary.tokenizer_id,
                    "verifier_artifact_digest": pair.verifier.implementation_artifact_digest,
                    "verifier_implementation_id": pair.verifier.implementation_id,
                }
                for pair in normalized_pairs
            ],
            "served_attestation_digest": attestation_digest,
            "suite_manifest_digest": manifest.digest,
        }
    )
    return VerifiedTokenizerSuiteV3(
        manifest=manifest,
        backend_pairs=normalized_pairs,
        served_attestation=attestation_object,
        served_attestation_authority_public_key=attestation_authority_public_key,
        served_attestation_digest=attestation_digest,
        binding_digest=binding_digest,
        _seal=_VERIFIED_SUITE_SEAL,
    )


@dataclass(frozen=True, slots=True)
class ExactTokenMeasurementV3:
    scope: TokenMeasurementScopeV3
    framing_digest: str
    text_digest: str
    byte_length: int
    suite_manifest_digest: str
    suite_binding_digest: str
    evidence_class: TokenizerEvidenceClassV3
    tokenizer_receipts: tuple[TokenizerReceipt, ...]

    def __post_init__(self) -> None:
        if type(self) is not ExactTokenMeasurementV3:
            raise TokenizerAttestationError("token measurement subclasses are forbidden")
        if type(self.scope) is not TokenMeasurementScopeV3:
            raise TokenizerAttestationError("token measurement scope must be exact")
        for label, value in (
            ("measurement framing digest", self.framing_digest),
            ("measurement text digest", self.text_digest),
            ("measurement suite manifest digest", self.suite_manifest_digest),
            ("measurement suite binding digest", self.suite_binding_digest),
        ):
            _digest(value, label)
        if type(self.byte_length) is not int or not 1 <= self.byte_length <= _MAX_TEXT_BYTES:
            raise TokenizerAttestationError("measurement byte length is out of bounds")
        if type(self.evidence_class) is not TokenizerEvidenceClassV3:
            raise TokenizerAttestationError("measurement evidence class must be exact")
        if type(self.tokenizer_receipts) is not tuple or not self.tokenizer_receipts:
            raise TokenizerAttestationError("measurement requires tokenizer receipts")
        if any(type(item) is not TokenizerReceipt for item in self.tokenizer_receipts):
            raise TokenizerAttestationError("measurement receipts must be exact records")
        for receipt in self.tokenizer_receipts:
            receipt.__post_init__()
        identities = tuple(receipt.tokenizer_id for receipt in self.tokenizer_receipts)
        if tuple(sorted(set(identities))) != identities:
            raise TokenizerAttestationError("measurement receipts must be unique and sorted")

    @property
    def matching_signature(self) -> tuple[Any, ...]:
        return (
            self.byte_length,
            tuple(
                (
                    receipt.tokenizer_id,
                    receipt.tokenizer_artifact_digest,
                    receipt.token_count,
                )
                for receipt in self.tokenizer_receipts
            ),
        )

    def commitment_record(self) -> dict[str, Any]:
        self.__post_init__()
        return {
            "byte_length": self.byte_length,
            "evidence_class": self.evidence_class.value,
            "framing_digest": self.framing_digest,
            "scope": self.scope.value,
            "suite_binding_digest": self.suite_binding_digest,
            "suite_manifest_digest": self.suite_manifest_digest,
            "text_digest": self.text_digest,
            "tokenizer_receipts": [item.to_dict() for item in self.tokenizer_receipts],
        }

    @property
    def digest(self) -> str:
        return _digest_json(self.commitment_record())


def _regenerate_token_ids(
    text: str,
    pair: TokenizerBackendPairV3,
) -> tuple[int, ...]:
    primary = pair.primary.encode_ordinary(text)
    verifier = pair.verifier.encode_ordinary(text)
    if primary != verifier:
        raise TokenizerAttestationError(
            "independent tokenizer implementations returned different token ids"
        )
    return primary


def _measure_exact_text_from_validated_suite(
    text: str,
    suite: VerifiedTokenizerSuiteV3,
    *,
    scope: TokenMeasurementScopeV3,
    framing_digest: str,
) -> ExactTokenMeasurementV3:
    _utf8_text(text, "measured text")
    if type(scope) is not TokenMeasurementScopeV3:
        raise TokenizerAttestationError("measurement scope must be exact")
    _digest(framing_digest, "measurement framing digest")
    encoded = text.encode("utf-8")
    text_digest = _sha256_bytes(encoded)
    receipts: list[TokenizerReceipt] = []
    for spec, pair in zip(suite.manifest.tokenizers, suite.backend_pairs, strict=True):
        token_ids = _regenerate_token_ids(text, pair)
        receipts.append(
            TokenizerReceipt(
                tokenizer_id=spec.tokenizer_id,
                tokenizer_artifact_digest=spec.digest,
                token_count=len(token_ids),
                token_ids_digest=canonical_token_ids_digest_v3(
                    token_ids,
                    tokenizer_spec_digest=spec.digest,
                    text_digest=text_digest,
                    scope=scope,
                    framing_digest=framing_digest,
                ),
            )
        )
    return ExactTokenMeasurementV3(
        scope=scope,
        framing_digest=framing_digest,
        text_digest=text_digest,
        byte_length=len(encoded),
        suite_manifest_digest=suite.manifest.digest,
        suite_binding_digest=suite.binding_digest,
        evidence_class=suite.manifest.evidence_class,
        tokenizer_receipts=tuple(receipts),
    )


def measure_exact_text_v3(
    text: str,
    suite: VerifiedTokenizerSuiteV3,
    *,
    scope: TokenMeasurementScopeV3,
    framing_digest: str,
) -> ExactTokenMeasurementV3:
    """Regenerate, compare, and commit exact token tuples for one exact UTF-8 text."""

    if type(suite) is not VerifiedTokenizerSuiteV3:
        raise TokenizerAttestationError("measurement requires a verified tokenizer suite")
    suite.__post_init__()
    return _measure_exact_text_from_validated_suite(
        text,
        suite,
        scope=scope,
        framing_digest=framing_digest,
    )


def verify_exact_text_measurement_v3(
    text: str,
    measurement: ExactTokenMeasurementV3,
    suite: VerifiedTokenizerSuiteV3,
) -> None:
    """Ignore receipt assertions and independently rebuild the entire measurement."""

    if type(measurement) is not ExactTokenMeasurementV3:
        raise TokenizerAttestationError("verification requires an exact token measurement")
    measurement.__post_init__()
    rebuilt = measure_exact_text_v3(
        text,
        suite,
        scope=measurement.scope,
        framing_digest=measurement.framing_digest,
    )
    if rebuilt != measurement:
        raise TokenizerAttestationError("token measurement differs from regenerated evidence")


@dataclass(frozen=True, slots=True)
class PromptPaddingPolicyV3:
    wrapper_digest: str
    minimum_padding_bytes: int
    maximum_padding_bytes: int
    padding_patterns: tuple[str, ...] = DEFAULT_PADDING_PATTERNS_V3
    maximum_candidates: int = 4096

    def __post_init__(self) -> None:
        if type(self) is not PromptPaddingPolicyV3:
            raise TokenizerAttestationError("prompt-padding policy subclasses are forbidden")
        _digest(self.wrapper_digest, "prompt wrapper digest")
        if (
            type(self.minimum_padding_bytes) is not int
            or type(self.maximum_padding_bytes) is not int
            or not 1 <= self.minimum_padding_bytes <= self.maximum_padding_bytes
            or self.maximum_padding_bytes > _MAX_PADDING_BYTES
        ):
            raise TokenizerAttestationError("padding byte bounds are invalid")
        if type(self.padding_patterns) is not tuple or not self.padding_patterns:
            raise TokenizerAttestationError("padding patterns must be a nonempty tuple")
        if any(type(item) is not str or not item for item in self.padding_patterns):
            raise TokenizerAttestationError("padding patterns must be nonempty exact text")
        if tuple(sorted(set(self.padding_patterns))) != self.padding_patterns:
            raise TokenizerAttestationError("padding patterns must be unique and sorted")
        for pattern in self.padding_patterns:
            if any(character not in " \t\n" for character in pattern):
                raise TokenizerAttestationError(
                    "padding patterns may contain only raw ASCII space, tab, and LF"
                )
            if len(pattern.encode("ascii")) > 64:
                raise TokenizerAttestationError("padding pattern exceeds its byte budget")
        if (
            type(self.maximum_candidates) is not int
            or not 1 <= self.maximum_candidates <= _MAX_PADDING_CANDIDATES
        ):
            raise TokenizerAttestationError("padding candidate bound is invalid")
        theoretical = (self.maximum_padding_bytes - self.minimum_padding_bytes + 1) * len(
            self.padding_patterns
        )
        if theoretical > self.maximum_candidates:
            raise TokenizerAttestationError(
                "padding policy candidate universe exceeds its precommitted bound"
            )

    def commitment_record(self) -> dict[str, Any]:
        self.__post_init__()
        return {
            "candidate_order": "byte-length-then-padding-utf8-lexicographic",
            "maximum_candidates": self.maximum_candidates,
            "maximum_padding_bytes": self.maximum_padding_bytes,
            "minimum_padding_bytes": self.minimum_padding_bytes,
            "padding_patterns": list(self.padding_patterns),
            "record_type": EXACT_PROMPT_PADDING_SCHEMA_V3,
            "schema_version": 3,
            "slot_marker_digest": _sha256_bytes(RAW_WHITESPACE_PADDING_SLOT_V3.encode("ascii")),
            "solution_order": ("common-signature-lexicographic-then-padding-utf8-lexicographic"),
            "wrapper_digest": self.wrapper_digest,
        }

    @property
    def digest(self) -> str:
        return _digest_json(self.commitment_record())


def _padding_candidates(policy: PromptPaddingPolicyV3) -> tuple[str, ...]:
    policy.__post_init__()
    candidates: set[str] = set()
    for byte_length in range(policy.minimum_padding_bytes, policy.maximum_padding_bytes + 1):
        for pattern in policy.padding_patterns:
            repetitions = (byte_length + len(pattern) - 1) // len(pattern)
            candidate = (pattern * repetitions)[:byte_length]
            candidates.add(candidate)
    ordered = tuple(
        sorted(candidates, key=lambda item: (len(item.encode("ascii")), item.encode("ascii")))
    )
    if len(ordered) > policy.maximum_candidates:
        raise TokenizerAttestationError("padding candidate generation exceeded its bound")
    return ordered


def _prompt_base(template: str) -> str:
    _utf8_text(template, "prompt template")
    if template.count(RAW_WHITESPACE_PADDING_SLOT_V3) != 1 or not template.endswith(
        RAW_WHITESPACE_PADDING_SLOT_V3
    ):
        raise TokenizerAttestationError(
            "prompt template must end in exactly one raw-whitespace padding slot"
        )
    return template[: -len(RAW_WHITESPACE_PADDING_SLOT_V3)]


@dataclass(frozen=True, slots=True)
class PromptMaterializationSideV3:
    final_prompt: str = field(repr=False)
    padding: str = field(repr=False)
    base_prompt_digest: str
    final_prompt_digest: str
    padding_digest: str
    padding_byte_length: int
    measurement: ExactTokenMeasurementV3

    def __post_init__(self) -> None:
        if type(self) is not PromptMaterializationSideV3:
            raise TokenizerAttestationError("prompt-materialization side subclasses are forbidden")
        _utf8_text(self.final_prompt, "materialized final prompt")
        _utf8_text(
            self.padding,
            "materialized prompt padding",
            maximum_bytes=_MAX_PADDING_BYTES,
        )
        if any(character not in " \t\n" for character in self.padding):
            raise TokenizerAttestationError("materialized prompt padding is not raw whitespace")
        if not self.final_prompt.endswith(self.padding):
            raise TokenizerAttestationError("final prompt does not end in its recorded padding")
        for label, value in (
            ("base prompt digest", self.base_prompt_digest),
            ("final prompt digest", self.final_prompt_digest),
            ("padding digest", self.padding_digest),
        ):
            _digest(value, label)
        encoded_padding = self.padding.encode("ascii")
        if self.padding_byte_length != len(encoded_padding):
            raise TokenizerAttestationError("padding byte length differs from its text")
        if _sha256_bytes(encoded_padding) != self.padding_digest:
            raise TokenizerAttestationError("padding differs from its digest")
        if _sha256_bytes(self.final_prompt.encode("utf-8")) != self.final_prompt_digest:
            raise TokenizerAttestationError("final prompt differs from its digest")
        base_prompt = self.final_prompt[: -len(self.padding)]
        if _sha256_bytes(base_prompt.encode("utf-8")) != self.base_prompt_digest:
            raise TokenizerAttestationError("prompt base differs from its digest")
        if type(self.measurement) is not ExactTokenMeasurementV3:
            raise TokenizerAttestationError("materialized prompt requires exact token measurement")
        self.measurement.__post_init__()
        if (
            self.measurement.scope is not TokenMeasurementScopeV3.FINAL_SOLVER_PROMPT
            or self.measurement.text_digest != self.final_prompt_digest
        ):
            raise TokenizerAttestationError(
                "prompt measurement is not bound to the exact final solver prompt"
            )

    def commitment_record(self) -> dict[str, Any]:
        self.__post_init__()
        return {
            "base_prompt_digest": self.base_prompt_digest,
            "final_prompt_digest": self.final_prompt_digest,
            "measurement": self.measurement.commitment_record(),
            "padding_byte_length": self.padding_byte_length,
            "padding_digest": self.padding_digest,
        }


@dataclass(frozen=True, slots=True)
class ExactPromptPairMaterializationV3:
    first: PromptMaterializationSideV3
    second: PromptMaterializationSideV3
    suite_manifest_digest: str
    suite_binding_digest: str
    padding_policy_digest: str
    evidence_class: TokenizerEvidenceClassV3
    common_signature_digest: str

    def __post_init__(self) -> None:
        if type(self) is not ExactPromptPairMaterializationV3:
            raise TokenizerAttestationError("prompt-pair materialization subclasses are forbidden")
        if (
            type(self.first) is not PromptMaterializationSideV3
            or type(self.second) is not PromptMaterializationSideV3
        ):
            raise TokenizerAttestationError("prompt pair requires exact materialization sides")
        self.first.__post_init__()
        self.second.__post_init__()
        for label, value in (
            ("pair suite manifest digest", self.suite_manifest_digest),
            ("pair suite binding digest", self.suite_binding_digest),
            ("pair padding policy digest", self.padding_policy_digest),
            ("pair common signature digest", self.common_signature_digest),
        ):
            _digest(value, label)
        if type(self.evidence_class) is not TokenizerEvidenceClassV3:
            raise TokenizerAttestationError("pair evidence class must be exact")
        measurements = (self.first.measurement, self.second.measurement)
        if any(
            item.suite_manifest_digest != self.suite_manifest_digest
            or item.suite_binding_digest != self.suite_binding_digest
            or item.evidence_class is not self.evidence_class
            for item in measurements
        ):
            raise TokenizerAttestationError("prompt pair measurements use different evidence")
        if self.first.measurement.matching_signature != self.second.measurement.matching_signature:
            raise TokenizerAttestationError(
                "final prompts differ in UTF-8 bytes or a preregistered tokenizer count"
            )
        expected_signature_digest = _digest_json(
            {
                "byte_length": self.first.measurement.byte_length,
                "tokenizer_counts": [
                    {
                        "token_count": receipt.token_count,
                        "tokenizer_artifact_digest": receipt.tokenizer_artifact_digest,
                        "tokenizer_id": receipt.tokenizer_id,
                    }
                    for receipt in self.first.measurement.tokenizer_receipts
                ],
            }
        )
        if self.common_signature_digest != expected_signature_digest:
            raise TokenizerAttestationError("common prompt signature differs from its evidence")

    def private_commitment_record(self) -> dict[str, Any]:
        self.__post_init__()
        return {
            "common_signature_digest": self.common_signature_digest,
            "evidence_class": self.evidence_class.value,
            "first": self.first.commitment_record(),
            "padding_policy_digest": self.padding_policy_digest,
            "record_type": EXACT_PROMPT_PADDING_SCHEMA_V3,
            "schema_version": 3,
            "second": self.second.commitment_record(),
            "suite_binding_digest": self.suite_binding_digest,
            "suite_manifest_digest": self.suite_manifest_digest,
        }

    @property
    def digest(self) -> str:
        return _digest_json(self.private_commitment_record())

    def public_commitment(self) -> dict[str, Any]:
        """Return a root commitment with no prompt, padding, token IDs, paths, or packet IDs."""

        return {
            "evidence_class": self.evidence_class.value,
            "materialization_digest": self.digest,
            "padding_policy_digest": self.padding_policy_digest,
            "record_type": EXACT_PROMPT_PADDING_SCHEMA_V3,
            "schema_version": 3,
            "suite_manifest_digest": self.suite_manifest_digest,
        }

    @property
    def public_commitment_digest(self) -> str:
        return _digest_json(self.public_commitment())


def _side_from_candidate(
    base_prompt: str,
    padding: str,
    measurement: ExactTokenMeasurementV3,
) -> PromptMaterializationSideV3:
    final_prompt = base_prompt + padding
    return PromptMaterializationSideV3(
        final_prompt=final_prompt,
        padding=padding,
        base_prompt_digest=_sha256_bytes(base_prompt.encode("utf-8")),
        final_prompt_digest=_sha256_bytes(final_prompt.encode("utf-8")),
        padding_digest=_sha256_bytes(padding.encode("ascii")),
        padding_byte_length=len(padding.encode("ascii")),
        measurement=measurement,
    )


def materialize_exact_prompt_pair_v3(
    first_prompt_template: str,
    second_prompt_template: str,
    suite: VerifiedTokenizerSuiteV3,
    policy: PromptPaddingPolicyV3,
) -> ExactPromptPairMaterializationV3:
    """Find the canonical bounded raw-whitespace padding for two exact final prompts.

    The function receives no arm label, packet id, source path, receipt field, or condition
    mapping and injects none.  All measurement and padding metadata stays evaluator-side.
    """

    if type(suite) is not VerifiedTokenizerSuiteV3:
        raise TokenizerAttestationError("prompt padding requires a verified tokenizer suite")
    suite.__post_init__()
    if type(policy) is not PromptPaddingPolicyV3:
        raise TokenizerAttestationError("prompt padding requires an exact policy")
    policy.__post_init__()
    first_base = _prompt_base(first_prompt_template)
    second_base = _prompt_base(second_prompt_template)
    candidates = _padding_candidates(policy)

    def candidate_map(
        base_prompt: str,
    ) -> dict[tuple[Any, ...], tuple[str, ExactTokenMeasurementV3]]:
        result: dict[tuple[Any, ...], tuple[str, ExactTokenMeasurementV3]] = {}
        for padding in candidates:
            final_prompt = base_prompt + padding
            measurement = _measure_exact_text_from_validated_suite(
                final_prompt,
                suite,
                scope=TokenMeasurementScopeV3.FINAL_SOLVER_PROMPT,
                framing_digest=policy.wrapper_digest,
            )
            signature = measurement.matching_signature
            previous = result.get(signature)
            if previous is None or padding.encode("ascii") < previous[0].encode("ascii"):
                result[signature] = (padding, measurement)
        return result

    first_candidates = candidate_map(first_base)
    second_candidates = candidate_map(second_base)
    common = sorted(set(first_candidates).intersection(second_candidates))
    if not common:
        raise PromptPaddingInfeasibleError(
            "bounded prompt-padding search found no byte-and-token exact solution"
        )
    signature = common[0]
    first_padding, first_measurement = first_candidates[signature]
    second_padding, second_measurement = second_candidates[signature]
    signature_digest = _digest_json(
        {
            "byte_length": first_measurement.byte_length,
            "tokenizer_counts": [
                {
                    "token_count": receipt.token_count,
                    "tokenizer_artifact_digest": receipt.tokenizer_artifact_digest,
                    "tokenizer_id": receipt.tokenizer_id,
                }
                for receipt in first_measurement.tokenizer_receipts
            ],
        }
    )
    return ExactPromptPairMaterializationV3(
        first=_side_from_candidate(first_base, first_padding, first_measurement),
        second=_side_from_candidate(second_base, second_padding, second_measurement),
        suite_manifest_digest=suite.manifest.digest,
        suite_binding_digest=suite.binding_digest,
        padding_policy_digest=policy.digest,
        evidence_class=suite.manifest.evidence_class,
        common_signature_digest=signature_digest,
    )


def verify_exact_prompt_pair_v3(
    materialization: ExactPromptPairMaterializationV3,
    first_prompt_template: str,
    second_prompt_template: str,
    suite: VerifiedTokenizerSuiteV3,
    policy: PromptPaddingPolicyV3,
    *,
    expected_public_commitment_digest: str,
) -> None:
    """Replay the finite search and independently regenerate both final prompt token tuples."""

    if type(materialization) is not ExactPromptPairMaterializationV3:
        raise TokenizerAttestationError("prompt-pair verifier requires an exact materialization")
    materialization.__post_init__()
    _digest(expected_public_commitment_digest, "expected public materialization commitment")
    if materialization.public_commitment_digest != expected_public_commitment_digest:
        raise TokenizerAttestationError("materialization differs from its public commitment")
    rebuilt = materialize_exact_prompt_pair_v3(
        first_prompt_template,
        second_prompt_template,
        suite,
        policy,
    )
    if rebuilt != materialization:
        raise TokenizerAttestationError(
            "prompt materialization differs from independently regenerated evidence"
        )
    verify_exact_text_measurement_v3(
        materialization.first.final_prompt,
        materialization.first.measurement,
        suite,
    )
    verify_exact_text_measurement_v3(
        materialization.second.final_prompt,
        materialization.second.measurement,
        suite,
    )


def require_strict_served_attested_v3(
    materialization: ExactPromptPairMaterializationV3,
    suite: VerifiedTokenizerSuiteV3,
    *,
    first_prompt_template: str,
    second_prompt_template: str,
    policy: PromptPaddingPolicyV3,
    expected_public_commitment_digest: str,
) -> None:
    """Replay exact final prompts, then fail closed unless all strict evidence binds."""

    if (
        type(materialization) is not ExactPromptPairMaterializationV3
        or type(suite) is not VerifiedTokenizerSuiteV3
    ):
        raise TokenizerAttestationError("strict gate requires exact verified evidence")
    materialization.__post_init__()
    suite.__post_init__()
    if (
        not suite.strict_served_attested_eligible
        or materialization.evidence_class is not TokenizerEvidenceClassV3.SERVED_ATTESTED
        or materialization.suite_manifest_digest != suite.manifest.digest
        or materialization.suite_binding_digest != suite.binding_digest
    ):
        raise TokenizerAttestationError(
            "strict served-tokenizer eligibility is absent; engineering_proxy only"
        )
    verify_exact_prompt_pair_v3(
        materialization,
        first_prompt_template,
        second_prompt_template,
        suite,
        policy,
        expected_public_commitment_digest=expected_public_commitment_digest,
    )


__all__ = [
    "DEFAULT_PADDING_PATTERNS_V3",
    "EXACT_PROMPT_PADDING_SCHEMA_V3",
    "RAW_WHITESPACE_PADDING_SLOT_V3",
    "SERVED_TOKENIZER_ATTESTATION_DOMAIN_V3",
    "SERVED_TOKENIZER_ATTESTATION_SCHEMA_V3",
    "TOKENIZER_ATTESTATION_SCHEMA_V3",
    "ExactPromptPairMaterializationV3",
    "ExactTokenMeasurementV3",
    "OfflineTokenizerBackendV3",
    "PromptMaterializationSideV3",
    "PromptPaddingInfeasibleError",
    "PromptPaddingPolicyV3",
    "ServedTokenizerAttestationV3",
    "TokenMeasurementScopeV3",
    "TokenizerAttestationError",
    "TokenizerBackendPairV3",
    "TokenizerEvidenceClassV3",
    "TokenizerSpecV3",
    "TokenizerSuiteManifestV3",
    "VerifiedTokenizerSuiteV3",
    "bind_tokenizer_suite_v3",
    "canonical_token_ids_digest_v3",
    "materialize_exact_prompt_pair_v3",
    "measure_exact_text_v3",
    "require_strict_served_attested_v3",
    "served_tokenizer_attestation_unsigned_record_v3",
    "verify_exact_prompt_pair_v3",
    "verify_exact_text_measurement_v3",
]
