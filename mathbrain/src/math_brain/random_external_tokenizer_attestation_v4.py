"""Cold-replayable packet padding and proxy-tokenizer evidence for pilot v4.

The v3 tokenizer module can attest an exact text, but its padding primitive is
specifically a two-prompt wrapper.  Packet materialization has a different authority
boundary: it must find one byte/token-count signature across every selected retrieval
draft without pretending that an offline tokenizer is the served tokenizer.  This
module therefore reuses the v3 exact-text measurement and independent-backend checks,
while defining a packet-specific finite padding policy and replay record.

Raw padding and final packet text stay private.  Public records contain only commitments
and fix served-tokenizer, served-model, and confirmatory authority to ``False``.
"""

from __future__ import annotations

import hashlib
import hmac
import re
from dataclasses import dataclass, field
from typing import Any

from .models import canonical_json
from .three_arm_tokenizer_attestation_v3 import (
    ExactTokenMeasurementV3,
    TokenizerEvidenceClassV3,
    TokenMeasurementScopeV3,
    VerifiedTokenizerSuiteV3,
    measure_exact_text_v3,
    verify_exact_text_measurement_v3,
)

RANDOM_EXTERNAL_PACKET_PADDING_SCHEMA_V4 = (
    "org.math-research-brain/random-external-packet-padding/v4"
)
RANDOM_EXTERNAL_TOKENIZER_ATTESTATION_SCHEMA_V4 = (
    "org.math-research-brain/random-external-tokenizer-attestation/v4"
)

_DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}\Z", re.ASCII)
_CASE_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/+-]{0,127}\Z", re.ASCII)
_MAX_PACKET_INPUTS = 24
_MAX_INPUT_BYTES = 16_384
_MAX_PADDING_BYTES = 16_384
_MAX_CANDIDATES = 100_000
_ATTESTATION_DOMAIN = "math-brain/random-external-tokenizer-attestation-v4"
_RECEIPT_DOMAIN = "math-brain/random-external-packet-padding-receipt-v4"
_FORMAT_DOMAIN = "math-brain/random-external-padded-packet-format-v4"
_COMMON_SIGNATURE_DOMAIN = "math-brain/random-external-common-packet-signature-v4"


class RandomExternalTokenizerError(ValueError):
    """Raised when exact packet token/padding evidence cannot be replayed."""


class RandomExternalPacketPaddingInfeasible(RandomExternalTokenizerError):
    """Raised when the finite padding universe has no common exact signature."""


def _sha256(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _domain_digest(domain: str, value: object) -> str:
    return _sha256(domain.encode("ascii") + b"\x00" + canonical_json(value).encode("utf-8"))


def _digest(value: object, label: str) -> str:
    if type(value) is not str or _DIGEST_RE.fullmatch(value) is None:
        raise RandomExternalTokenizerError(f"{label} must be a canonical SHA-256 digest")
    return value


def _case_id(value: object, label: str) -> str:
    if type(value) is not str or _CASE_ID_RE.fullmatch(value) is None:
        raise RandomExternalTokenizerError(f"{label} is invalid")
    return value


def _utf8_text(
    value: object,
    label: str,
    *,
    allow_empty: bool = False,
    maximum_bytes: int = _MAX_INPUT_BYTES + _MAX_PADDING_BYTES,
) -> str:
    if type(value) is not str:
        raise RandomExternalTokenizerError(f"{label} must be exact text")
    try:
        encoded = value.encode("utf-8", errors="strict")
    except UnicodeEncodeError as error:
        raise RandomExternalTokenizerError(f"{label} must be valid UTF-8") from error
    if not allow_empty and not value:
        raise RandomExternalTokenizerError(f"{label} must not be empty")
    if len(encoded) > maximum_bytes:
        raise RandomExternalTokenizerError(f"{label} exceeds its byte budget")
    return value


@dataclass(frozen=True, slots=True)
class PacketPaddingPolicyV4:
    """A bounded raw-space/tab padding universe for exact packet matching."""

    base_format_template_digest: str
    minimum_padding_bytes: int
    maximum_padding_bytes: int
    padding_patterns: tuple[str, ...] = ("\t", " ", " \t")
    maximum_candidates: int = 49_155

    def __post_init__(self) -> None:
        if type(self) is not PacketPaddingPolicyV4:
            raise RandomExternalTokenizerError("packet-padding policy subclasses are forbidden")
        _digest(self.base_format_template_digest, "packet-padding base format")
        if (
            type(self.minimum_padding_bytes) is not int
            or type(self.maximum_padding_bytes) is not int
            or not 0 <= self.minimum_padding_bytes <= self.maximum_padding_bytes
            or self.maximum_padding_bytes > _MAX_PADDING_BYTES
        ):
            raise RandomExternalTokenizerError("packet-padding byte bounds are invalid")
        if type(self.padding_patterns) is not tuple or not self.padding_patterns:
            raise RandomExternalTokenizerError("packet-padding patterns must be a nonempty tuple")
        if tuple(sorted(set(self.padding_patterns))) != self.padding_patterns:
            raise RandomExternalTokenizerError("packet-padding patterns must be unique and sorted")
        for pattern in self.padding_patterns:
            if type(pattern) is not str or not pattern:
                raise RandomExternalTokenizerError("packet-padding patterns must be exact text")
            if any(character not in " \t" for character in pattern):
                raise RandomExternalTokenizerError(
                    "packet padding may contain only ASCII space and tab"
                )
            if len(pattern.encode("ascii")) > 64:
                raise RandomExternalTokenizerError("packet-padding pattern is too long")
        if (
            type(self.maximum_candidates) is not int
            or not 1 <= self.maximum_candidates <= _MAX_CANDIDATES
        ):
            raise RandomExternalTokenizerError("packet-padding candidate bound is invalid")
        theoretical = (self.maximum_padding_bytes - self.minimum_padding_bytes + 1) * len(
            self.padding_patterns
        )
        if theoretical > self.maximum_candidates:
            raise RandomExternalTokenizerError(
                "packet-padding universe exceeds its precommitted candidate bound"
            )

    def commitment_record(self) -> dict[str, object]:
        self.__post_init__()
        return {
            "base_format_template_digest": self.base_format_template_digest,
            "candidate_order": "byte-length-then-padding-ascii-lexicographic",
            "maximum_candidates": self.maximum_candidates,
            "maximum_padding_bytes": self.maximum_padding_bytes,
            "minimum_padding_bytes": self.minimum_padding_bytes,
            "padding_alphabet": "ascii-space-and-tab-only",
            "padding_patterns": list(self.padding_patterns),
            "schema": RANDOM_EXTERNAL_PACKET_PADDING_SCHEMA_V4,
            "solution_order": "common-signature-then-per-case-padding-lexicographic",
        }

    @property
    def digest(self) -> str:
        return _domain_digest(_ATTESTATION_DOMAIN + "/policy", self.commitment_record())

    @property
    def output_format_template_digest(self) -> str:
        return _domain_digest(
            _FORMAT_DOMAIN,
            {
                "base_format_template_digest": self.base_format_template_digest,
                "packet_padding_policy_digest": self.digest,
                "padding_position": "after-exact-two-line-render-fragment",
                "preserves_line_count": True,
            },
        )


@dataclass(frozen=True, slots=True)
class PacketPaddingInputV4:
    case_id: str
    draft_digest: str
    input_text: str = field(repr=False)
    input_format_template_digest: str

    def __post_init__(self) -> None:
        if type(self) is not PacketPaddingInputV4:
            raise RandomExternalTokenizerError("packet-padding input subclasses are forbidden")
        _case_id(self.case_id, "packet-padding case id")
        _digest(self.draft_digest, "packet-padding draft")
        _utf8_text(
            self.input_text,
            "packet-padding input text",
            maximum_bytes=_MAX_INPUT_BYTES,
        )
        _digest(self.input_format_template_digest, "packet-padding input format")

    @property
    def input_text_digest(self) -> str:
        return _sha256(self.input_text.encode("utf-8"))

    @property
    def input_byte_length(self) -> int:
        return len(self.input_text.encode("utf-8"))

    def commitment_record(self) -> dict[str, object]:
        self.__post_init__()
        return {
            "case_id": self.case_id,
            "draft_digest": self.draft_digest,
            "input_byte_length": self.input_byte_length,
            "input_format_template_digest": self.input_format_template_digest,
            "input_text_digest": self.input_text_digest,
        }


@dataclass(frozen=True, slots=True)
class ExactPacketPaddingReceiptV4:
    case_id: str
    draft_digest: str
    final_text: str = field(repr=False)
    padding: str = field(repr=False)
    input_text_digest: str
    input_byte_length: int
    final_text_digest: str
    padding_digest: str
    padding_byte_length: int
    input_format_template_digest: str
    output_format_template_digest: str
    measurement: ExactTokenMeasurementV3
    content_digest: str = field(init=False)

    def __post_init__(self) -> None:
        if type(self) is not ExactPacketPaddingReceiptV4:
            raise RandomExternalTokenizerError("packet-padding receipt subclasses are forbidden")
        self._validate_fields()
        object.__setattr__(
            self,
            "content_digest",
            _domain_digest(_RECEIPT_DOMAIN, self._payload()),
        )

    def _validate_fields(self) -> None:
        _case_id(self.case_id, "packet-padding receipt case id")
        _digest(self.draft_digest, "packet-padding receipt draft")
        _utf8_text(self.final_text, "packet-padding final text")
        _utf8_text(
            self.padding,
            "packet-padding whitespace",
            allow_empty=True,
            maximum_bytes=_MAX_PADDING_BYTES,
        )
        if any(character not in " \t" for character in self.padding):
            raise RandomExternalTokenizerError("packet-padding receipt contains non-padding text")
        for value, label in (
            (self.input_text_digest, "packet-padding input text"),
            (self.final_text_digest, "packet-padding final text"),
            (self.padding_digest, "packet-padding whitespace"),
            (self.input_format_template_digest, "packet-padding input format"),
            (self.output_format_template_digest, "packet-padding output format"),
        ):
            _digest(value, label)
        padding_raw = self.padding.encode("ascii")
        final_raw = self.final_text.encode("utf-8")
        if type(self.padding_byte_length) is not int or (
            self.padding_byte_length != len(padding_raw)
        ):
            raise RandomExternalTokenizerError("packet-padding byte length is stale")
        if (
            type(self.input_byte_length) is not int
            or not 1 <= self.input_byte_length <= _MAX_INPUT_BYTES
        ):
            raise RandomExternalTokenizerError("packet-padding input byte length is invalid")
        if len(final_raw) != self.input_byte_length + self.padding_byte_length:
            raise RandomExternalTokenizerError("packet-padding final byte length is inconsistent")
        base_text = (
            self.final_text
            if self.padding_byte_length == 0
            else self.final_text[: -len(self.padding)]
        )
        if self.padding_byte_length and not self.final_text.endswith(self.padding):
            raise RandomExternalTokenizerError("packet-padding final text lacks its suffix")
        if _sha256(base_text.encode("utf-8")) != self.input_text_digest:
            raise RandomExternalTokenizerError("packet-padding input text digest is stale")
        if _sha256(final_raw) != self.final_text_digest:
            raise RandomExternalTokenizerError("packet-padding final text digest is stale")
        if _sha256(padding_raw) != self.padding_digest:
            raise RandomExternalTokenizerError("packet-padding whitespace digest is stale")
        if type(self.measurement) is not ExactTokenMeasurementV3:
            raise RandomExternalTokenizerError("packet-padding requires exact token evidence")
        self.measurement.__post_init__()
        if (
            self.measurement.scope is not TokenMeasurementScopeV3.PACKET_TEXT
            or self.measurement.text_digest != self.final_text_digest
            or self.measurement.byte_length != len(final_raw)
        ):
            raise RandomExternalTokenizerError(
                "packet-padding measurement is not bound to the final packet text"
            )

    def _payload(self) -> dict[str, object]:
        return {
            "case_id": self.case_id,
            "draft_digest": self.draft_digest,
            "final_text_digest": self.final_text_digest,
            "input_byte_length": self.input_byte_length,
            "input_format_template_digest": self.input_format_template_digest,
            "input_text_digest": self.input_text_digest,
            "measurement": self.measurement.commitment_record(),
            "output_format_template_digest": self.output_format_template_digest,
            "padding_byte_length": self.padding_byte_length,
            "padding_digest": self.padding_digest,
        }

    def validate_current(self) -> None:
        if type(self) is not ExactPacketPaddingReceiptV4:
            raise RandomExternalTokenizerError("packet-padding receipt subclasses are forbidden")
        self._validate_fields()
        expected = _domain_digest(_RECEIPT_DOMAIN, self._payload())
        if not hmac.compare_digest(self.content_digest, expected):
            raise RandomExternalTokenizerError("packet-padding receipt digest is stale")

    def commitment_record(self) -> dict[str, object]:
        self.validate_current()
        return self._payload() | {"content_digest": self.content_digest}


def _normalized_inputs(
    inputs: tuple[PacketPaddingInputV4, ...] | list[PacketPaddingInputV4],
) -> tuple[PacketPaddingInputV4, ...]:
    if type(inputs) not in {list, tuple}:
        raise RandomExternalTokenizerError("packet-padding inputs must be a bounded sequence")
    result = tuple(inputs)
    if len(result) > _MAX_PACKET_INPUTS or any(
        type(item) is not PacketPaddingInputV4 for item in result
    ):
        raise RandomExternalTokenizerError("packet-padding input universe is invalid")
    for item in result:
        item.__post_init__()
    case_ids = tuple(item.case_id for item in result)
    if tuple(sorted(set(case_ids))) != case_ids:
        raise RandomExternalTokenizerError("packet-padding inputs must be unique and sorted")
    if len({item.draft_digest for item in result}) != len(result):
        raise RandomExternalTokenizerError("packet-padding inputs repeat a draft")
    return result


def _padding_candidates(policy: PacketPaddingPolicyV4) -> tuple[str, ...]:
    policy.__post_init__()
    candidates: set[str] = set()
    for byte_length in range(policy.minimum_padding_bytes, policy.maximum_padding_bytes + 1):
        if byte_length == 0:
            candidates.add("")
            continue
        for pattern in policy.padding_patterns:
            repetitions = (byte_length + len(pattern) - 1) // len(pattern)
            candidates.add((pattern * repetitions)[:byte_length])
    ordered = tuple(
        sorted(candidates, key=lambda value: (len(value.encode("ascii")), value.encode("ascii")))
    )
    if len(ordered) > policy.maximum_candidates:
        raise RandomExternalTokenizerError("packet-padding generation exceeded its bound")
    return ordered


def _signature_record(measurement: ExactTokenMeasurementV3) -> dict[str, object]:
    measurement.__post_init__()
    return {
        "byte_length": measurement.byte_length,
        "tokenizer_counts": [
            {
                "token_count": receipt.token_count,
                "tokenizer_artifact_digest": receipt.tokenizer_artifact_digest,
                "tokenizer_id": receipt.tokenizer_id,
            }
            for receipt in measurement.tokenizer_receipts
        ],
    }


@dataclass(frozen=True, slots=True)
class RandomExternalTokenizerAttestationV4:
    inputs: tuple[PacketPaddingInputV4, ...] = field(repr=False)
    suite: VerifiedTokenizerSuiteV3 = field(repr=False, compare=False)
    policy: PacketPaddingPolicyV4
    receipts: tuple[ExactPacketPaddingReceiptV4, ...] = field(repr=False)
    input_universe_digest: str
    receipt_universe_digest: str
    common_signature_digest: str | None
    content_digest: str = field(init=False)

    def __post_init__(self) -> None:
        if type(self) is not RandomExternalTokenizerAttestationV4:
            raise RandomExternalTokenizerError("v4 tokenizer-attestation subclasses are forbidden")
        self._validate_fields()
        object.__setattr__(
            self,
            "content_digest",
            _domain_digest(_ATTESTATION_DOMAIN, self._payload()),
        )

    @property
    def served_tokenizer_identity_verified(self) -> bool:
        return False

    @property
    def served_model_identity_verified(self) -> bool:
        return False

    @property
    def confirmatory_claim_authorized(self) -> bool:
        return False

    def _validate_fields(self) -> None:
        normalized = _normalized_inputs(self.inputs)
        if normalized != self.inputs:
            raise RandomExternalTokenizerError("tokenizer-attestation inputs are not canonical")
        if type(self.suite) is not VerifiedTokenizerSuiteV3:
            raise RandomExternalTokenizerError("tokenizer attestation requires a verified suite")
        self.suite.__post_init__()
        if self.suite.manifest.evidence_class is not TokenizerEvidenceClassV3.ENGINEERING_PROXY:
            raise RandomExternalTokenizerError(
                "random-external v4 fixes tokenizer evidence to engineering_proxy"
            )
        if type(self.policy) is not PacketPaddingPolicyV4:
            raise RandomExternalTokenizerError("tokenizer attestation requires an exact policy")
        self.policy.__post_init__()
        if self.inputs and any(
            item.input_format_template_digest != self.policy.base_format_template_digest
            for item in self.inputs
        ):
            raise RandomExternalTokenizerError("packet input format differs from padding policy")
        if type(self.receipts) is not tuple or len(self.receipts) != len(self.inputs):
            raise RandomExternalTokenizerError(
                "tokenizer attestation requires one receipt per input"
            )
        if any(type(item) is not ExactPacketPaddingReceiptV4 for item in self.receipts):
            raise RandomExternalTokenizerError("tokenizer attestation receipts must be exact")
        for receipt in self.receipts:
            receipt.validate_current()
        if tuple(receipt.case_id for receipt in self.receipts) != tuple(
            item.case_id for item in self.inputs
        ):
            raise RandomExternalTokenizerError("tokenizer receipt and input orders differ")
        input_by_case = {item.case_id: item for item in self.inputs}
        for receipt in self.receipts:
            source = input_by_case[receipt.case_id]
            if (
                receipt.draft_digest != source.draft_digest
                or receipt.input_text_digest != source.input_text_digest
                or receipt.input_byte_length != source.input_byte_length
                or receipt.input_format_template_digest != source.input_format_template_digest
                or receipt.output_format_template_digest
                != self.policy.output_format_template_digest
                or not receipt.final_text.startswith(source.input_text)
                or receipt.measurement.suite_manifest_digest != self.suite.manifest.digest
                or receipt.measurement.suite_binding_digest != self.suite.binding_digest
                or receipt.measurement.evidence_class
                is not TokenizerEvidenceClassV3.ENGINEERING_PROXY
                or receipt.measurement.framing_digest != self.policy.digest
            ):
                raise RandomExternalTokenizerError("tokenizer receipt lineage is inconsistent")
            verify_exact_text_measurement_v3(
                receipt.final_text,
                receipt.measurement,
                self.suite,
            )
        for value, label in (
            (self.input_universe_digest, "tokenizer input universe"),
            (self.receipt_universe_digest, "tokenizer receipt universe"),
        ):
            _digest(value, label)
        expected_inputs = _domain_digest(
            _ATTESTATION_DOMAIN + "/inputs",
            [item.commitment_record() for item in self.inputs],
        )
        expected_receipts = _domain_digest(
            _ATTESTATION_DOMAIN + "/receipts",
            [item.commitment_record() for item in self.receipts],
        )
        if self.input_universe_digest != expected_inputs:
            raise RandomExternalTokenizerError("tokenizer input-universe digest is stale")
        if self.receipt_universe_digest != expected_receipts:
            raise RandomExternalTokenizerError("tokenizer receipt-universe digest is stale")
        if not self.receipts:
            if self.common_signature_digest is not None:
                raise RandomExternalTokenizerError(
                    "empty tokenizer universe has no common signature"
                )
        else:
            _digest(self.common_signature_digest, "tokenizer common signature")
            signatures = {receipt.measurement.matching_signature for receipt in self.receipts}
            if len(signatures) != 1:
                raise RandomExternalTokenizerError(
                    "packet tokenizer signatures do not match exactly"
                )
            expected_signature = _domain_digest(
                _COMMON_SIGNATURE_DOMAIN,
                _signature_record(self.receipts[0].measurement),
            )
            if self.common_signature_digest != expected_signature:
                raise RandomExternalTokenizerError("tokenizer common-signature digest is stale")

    def _payload(self) -> dict[str, object]:
        return {
            "common_signature_digest": self.common_signature_digest,
            "confirmatory_claim_authorized": False,
            "evidence_class": TokenizerEvidenceClassV3.ENGINEERING_PROXY.value,
            "input_count": len(self.inputs),
            "input_universe_digest": self.input_universe_digest,
            "packet_padding_policy_digest": self.policy.digest,
            "receipt_universe_digest": self.receipt_universe_digest,
            "schema": RANDOM_EXTERNAL_TOKENIZER_ATTESTATION_SCHEMA_V4,
            "served_model_identity_verified": False,
            "served_tokenizer_identity_verified": False,
            "suite_binding_digest": self.suite.binding_digest,
            "suite_manifest_digest": self.suite.manifest.digest,
        }

    def validate_current(self) -> None:
        if type(self) is not RandomExternalTokenizerAttestationV4:
            raise RandomExternalTokenizerError("v4 tokenizer-attestation subclasses are forbidden")
        self._validate_fields()
        expected = _domain_digest(_ATTESTATION_DOMAIN, self._payload())
        if not hmac.compare_digest(self.content_digest, expected):
            raise RandomExternalTokenizerError("v4 tokenizer-attestation digest is stale")

    def public_commitment(self) -> dict[str, object]:
        self.validate_current()
        return self._payload() | {"tokenizer_attestation_digest": self.content_digest}


def materialize_random_external_tokenizer_attestation_v4(
    inputs: tuple[PacketPaddingInputV4, ...] | list[PacketPaddingInputV4],
    *,
    suite: VerifiedTokenizerSuiteV3,
    policy: PacketPaddingPolicyV4,
) -> RandomExternalTokenizerAttestationV4:
    """Find the canonical common packet signature and bind exact token vectors."""

    normalized_inputs = _normalized_inputs(inputs)
    if type(suite) is not VerifiedTokenizerSuiteV3:
        raise RandomExternalTokenizerError("packet materialization requires a verified suite")
    suite.__post_init__()
    if suite.manifest.evidence_class is not TokenizerEvidenceClassV3.ENGINEERING_PROXY:
        raise RandomExternalTokenizerError(
            "random-external v4 fixes tokenizer evidence to engineering_proxy"
        )
    if type(policy) is not PacketPaddingPolicyV4:
        raise RandomExternalTokenizerError("packet materialization requires an exact policy")
    policy.__post_init__()
    if normalized_inputs and any(
        item.input_format_template_digest != policy.base_format_template_digest
        for item in normalized_inputs
    ):
        raise RandomExternalTokenizerError("packet input format differs from padding policy")
    input_digest = _domain_digest(
        _ATTESTATION_DOMAIN + "/inputs",
        [item.commitment_record() for item in normalized_inputs],
    )
    if not normalized_inputs:
        return RandomExternalTokenizerAttestationV4(
            inputs=(),
            suite=suite,
            policy=policy,
            receipts=(),
            input_universe_digest=input_digest,
            receipt_universe_digest=_domain_digest(
                _ATTESTATION_DOMAIN + "/receipts",
                [],
            ),
            common_signature_digest=None,
        )
    candidates = _padding_candidates(policy)
    candidate_maps: list[dict[tuple[Any, ...], tuple[str, ExactTokenMeasurementV3]]] = []
    for item in normalized_inputs:
        by_signature: dict[tuple[Any, ...], tuple[str, ExactTokenMeasurementV3]] = {}
        for padding in candidates:
            final_text = item.input_text + padding
            measurement = measure_exact_text_v3(
                final_text,
                suite,
                scope=TokenMeasurementScopeV3.PACKET_TEXT,
                framing_digest=policy.digest,
            )
            signature = measurement.matching_signature
            previous = by_signature.get(signature)
            if previous is None or padding.encode("ascii") < previous[0].encode("ascii"):
                by_signature[signature] = (padding, measurement)
        candidate_maps.append(by_signature)
    common = set(candidate_maps[0])
    for candidate_map in candidate_maps[1:]:
        common.intersection_update(candidate_map)
    if not common:
        raise RandomExternalPacketPaddingInfeasible(
            "bounded packet-padding search found no common byte-and-token signature"
        )
    signature = min(common)
    receipts: list[ExactPacketPaddingReceiptV4] = []
    for item, candidate_map in zip(normalized_inputs, candidate_maps, strict=True):
        padding, measurement = candidate_map[signature]
        final_text = item.input_text + padding
        receipts.append(
            ExactPacketPaddingReceiptV4(
                case_id=item.case_id,
                draft_digest=item.draft_digest,
                final_text=final_text,
                padding=padding,
                input_text_digest=item.input_text_digest,
                input_byte_length=item.input_byte_length,
                final_text_digest=_sha256(final_text.encode("utf-8")),
                padding_digest=_sha256(padding.encode("ascii")),
                padding_byte_length=len(padding.encode("ascii")),
                input_format_template_digest=item.input_format_template_digest,
                output_format_template_digest=policy.output_format_template_digest,
                measurement=measurement,
            )
        )
    receipt_tuple = tuple(receipts)
    return RandomExternalTokenizerAttestationV4(
        inputs=normalized_inputs,
        suite=suite,
        policy=policy,
        receipts=receipt_tuple,
        input_universe_digest=input_digest,
        receipt_universe_digest=_domain_digest(
            _ATTESTATION_DOMAIN + "/receipts",
            [item.commitment_record() for item in receipt_tuple],
        ),
        common_signature_digest=_domain_digest(
            _COMMON_SIGNATURE_DOMAIN,
            _signature_record(receipt_tuple[0].measurement),
        ),
    )


def cold_replay_random_external_tokenizer_attestation_v4(
    inputs: tuple[PacketPaddingInputV4, ...] | list[PacketPaddingInputV4],
    *,
    suite: VerifiedTokenizerSuiteV3,
    policy: PacketPaddingPolicyV4,
    expected_tokenizer_attestation_digest: str,
) -> RandomExternalTokenizerAttestationV4:
    """Rebuild from raw packet inputs; no nominal attestation object is accepted."""

    expected = _digest(
        expected_tokenizer_attestation_digest,
        "expected v4 tokenizer attestation",
    )
    rebuilt = materialize_random_external_tokenizer_attestation_v4(
        inputs,
        suite=suite,
        policy=policy,
    )
    if not hmac.compare_digest(rebuilt.content_digest, expected):
        raise RandomExternalTokenizerError(
            "cold replay differs from the expected v4 tokenizer attestation"
        )
    return rebuilt


__all__ = [
    "RANDOM_EXTERNAL_PACKET_PADDING_SCHEMA_V4",
    "RANDOM_EXTERNAL_TOKENIZER_ATTESTATION_SCHEMA_V4",
    "ExactPacketPaddingReceiptV4",
    "PacketPaddingInputV4",
    "PacketPaddingPolicyV4",
    "RandomExternalPacketPaddingInfeasible",
    "RandomExternalTokenizerAttestationV4",
    "RandomExternalTokenizerError",
    "cold_replay_random_external_tokenizer_attestation_v4",
    "materialize_random_external_tokenizer_attestation_v4",
]
