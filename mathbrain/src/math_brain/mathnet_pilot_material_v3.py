"""Full-source, gold-separated MathNet material for the engineering pilot.

The earlier MathNet bank is a one-row-per-event acquisition smoke primitive.  It cannot
support joint domain/difficulty curation because it discards the rest of the eligible pool
before classification.  This module instead streams every pinned row into two structurally
separate artifacts:

* a gold-free universe containing problem text, opaque coordinates, raw language state,
  and event-family commitments; and
* a private vault containing exact-rational annotation evidence under a keyed public
  commitment.

Null source language is never rewritten to ``English``.  A fully filtered null-language row
remains an audit-queue candidate until two independent blinded language reviews authorize it.
Bare material is an engineering primitive; official authority is supplied only by the
acquisition wrapper in :mod:`math_brain.mathnet_acquisition_v3`.
"""

from __future__ import annotations

import hashlib
import hmac
import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import StrEnum

from .mathnet_bank_v3 import (
    MATHNET_HF_ROW_COUNT_V3,
    MathNetEligibilityCodeV3,
    MathNetPrivateGoldV3,
    MathNetSourceBindingV3,
    _advance_source_rows_state_v3,
    _initial_source_rows_state_v3,
    _opaque_source_digest_v3,
    _read_mathnet_row_v3,
    _screen_mathnet_row_fields_for_pilot_v3,
    _source_row_commitment_v3,
    synthetic_mathnet_source_binding_v3,
)
from .models import canonical_json

MATHNET_PILOT_MATERIAL_SCHEMA_V3 = "org.math-research-brain/mathnet-pilot-material"
MATHNET_PILOT_MATERIAL_VERSION_V3 = 3
MATHNET_PILOT_RIGHTS_STATE_V3 = "local_analysis_only"

_DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}\Z")
_HMAC_RE = re.compile(r"hmac-sha256:[0-9a-f]{64}\Z")
_CASE_ID_RE = re.compile(r"mathnet-pilot:[0-9a-f]{64}\Z")

_ROW_COORDINATE_DOMAIN = "org.math-research-brain/mathnet-pilot-row-coordinate/v3"
_GOLD_FREE_ROW_DOMAIN = "org.math-research-brain/mathnet-pilot-gold-free-row/v3"
_GOLD_FREE_PUBLIC_ROW_DOMAIN = "org.math-research-brain/mathnet-pilot-gold-free-public-row/v3"
_EVENT_FAMILY_DOMAIN = "org.math-research-brain/mathnet-source-cluster/v3"
_PUBLIC_SOURCE_ROW_DOMAIN = "org.math-research-brain/mathnet-pilot-source-row/v3"
_PUBLIC_ROWS_CHAIN_DOMAIN = b"math-brain/mathnet-pilot-v3/public-source-rows\x00"
_COORDINATE_UNIVERSE_DOMAIN = "org.math-research-brain/mathnet-pilot-coordinate-universe/v3"
_UNIVERSE_DOMAIN = "org.math-research-brain/mathnet-pilot-universe/v3"
_UNIVERSE_PUBLIC_DOMAIN = "org.math-research-brain/mathnet-pilot-universe-public/v3"
_VAULT_ENTRY_DOMAIN = "org.math-research-brain/mathnet-pilot-vault-entry/v3"
_VAULT_PRIVATE_DOMAIN = "org.math-research-brain/mathnet-pilot-vault-private/v3"
_VAULT_KEY_DOMAIN = b"math-brain/mathnet-pilot-v3/vault-key\x00"
_VAULT_HMAC_DOMAIN = b"math-brain/mathnet-pilot-v3/vault-content\x00"
_VAULT_PUBLIC_DOMAIN = "org.math-research-brain/mathnet-pilot-vault-public/v3"
_MATERIAL_DOMAIN = "org.math-research-brain/mathnet-pilot-material/v3"
_MATERIAL_PUBLIC_DOMAIN = "org.math-research-brain/mathnet-pilot-material-public/v3"


def _digest(value: object, *, domain: str) -> str:
    return (
        "sha256:"
        + hashlib.sha256(
            canonical_json({"domain": domain, "payload": value}).encode("utf-8")
        ).hexdigest()
    )


def _text_digest(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _validate_digest(value: object, label: str) -> str:
    if type(value) is not str or _DIGEST_RE.fullmatch(value) is None:
        raise ValueError(f"{label} must be a canonical SHA-256 digest")
    return value


def _validate_hmac(value: object, label: str) -> str:
    if type(value) is not str or _HMAC_RE.fullmatch(value) is None:
        raise ValueError(f"{label} must be a canonical HMAC-SHA-256 commitment")
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


def _validate_vault_key(value: object) -> bytes:
    if type(value) is not bytes or not 32 <= len(value) <= 64:
        raise ValueError("MathNet pilot vault key must contain 32 to 64 immutable bytes")
    return value


class MathNetPilotLanguageStateV3(StrEnum):
    SOURCE_ENGLISH = "source_english"
    NULL_AUDIT_REQUIRED = "source_null_audit_required"


class MathNetPilotMaterialCodeV3(StrEnum):
    SOURCE_ENGLISH_ELIGIBLE = "source_english_eligible"
    NULL_LANGUAGE_AUDIT_QUEUE = "null_language_audit_queue"
    NON_ENGLISH = "non_english"
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
class GoldFreeMathNetPilotRowV3:
    source_binding_digest: str
    source_ordinal: int
    opaque_source_digest: str
    problem_text: str = field(repr=False)
    problem_text_digest: str
    event_family_digest: str
    raw_language_state: MathNetPilotLanguageStateV3
    selection_coordinate_digest: str
    rights_state: str = MATHNET_PILOT_RIGHTS_STATE_V3
    blind_gold_audit_required: bool = True
    content_digest: str = field(init=False, repr=False)
    case_id: str = field(init=False)

    def __post_init__(self) -> None:
        if type(self) is not GoldFreeMathNetPilotRowV3:
            raise ValueError("MathNet pilot row subclasses are forbidden")
        self._validate_fields()
        object.__setattr__(
            self,
            "case_id",
            "mathnet-pilot:" + self.selection_coordinate_digest.removeprefix("sha256:"),
        )
        object.__setattr__(
            self,
            "content_digest",
            _digest(self._private_payload(), domain=_GOLD_FREE_ROW_DOMAIN),
        )

    def _coordinate_payload(self) -> dict[str, object]:
        return {
            "event_family_digest": self.event_family_digest,
            "opaque_source_digest": self.opaque_source_digest,
            "problem_text_digest": self.problem_text_digest,
            "raw_language_state": self.raw_language_state.value,
            "source_binding_digest": self.source_binding_digest,
            "source_ordinal": self.source_ordinal,
        }

    def _validate_fields(self) -> None:
        for label, value in (
            ("source-binding digest", self.source_binding_digest),
            ("opaque-source digest", self.opaque_source_digest),
            ("problem-text digest", self.problem_text_digest),
            ("event-family digest", self.event_family_digest),
            ("selection-coordinate digest", self.selection_coordinate_digest),
        ):
            _validate_digest(value, f"MathNet pilot {label}")
        if type(self.source_ordinal) is not int or not 0 <= self.source_ordinal < (
            MATHNET_HF_ROW_COUNT_V3
        ):
            raise ValueError("MathNet pilot source ordinal is invalid")
        _bounded_text(self.problem_text, "MathNet pilot problem text", maximum=128_000)
        if self.problem_text_digest != _text_digest(self.problem_text):
            raise ValueError("MathNet pilot problem-text digest is stale")
        if type(self.raw_language_state) is not MathNetPilotLanguageStateV3:
            raise ValueError("MathNet pilot raw language state must be exact")
        if self.rights_state != MATHNET_PILOT_RIGHTS_STATE_V3:
            raise ValueError("MathNet pilot rights state is unsupported")
        if self.blind_gold_audit_required is not True:
            raise ValueError("MathNet pilot rows require a blind gold audit")
        expected_coordinate = _digest(
            self._coordinate_payload(),
            domain=_ROW_COORDINATE_DOMAIN,
        )
        if not hmac.compare_digest(self.selection_coordinate_digest, expected_coordinate):
            raise ValueError("MathNet pilot selection coordinate is stale")

    def _private_payload(self) -> dict[str, object]:
        return {
            "blind_gold_audit_required": self.blind_gold_audit_required,
            "problem_text": self.problem_text,
            "rights_state": self.rights_state,
            "selection_coordinate": self._coordinate_payload(),
            "selection_coordinate_digest": self.selection_coordinate_digest,
        }

    def validate_current(self) -> None:
        if type(self) is not GoldFreeMathNetPilotRowV3:
            raise ValueError("MathNet pilot row subclasses are forbidden")
        self._validate_fields()
        expected_id = "mathnet-pilot:" + self.selection_coordinate_digest.removeprefix("sha256:")
        if (
            type(self.case_id) is not str
            or _CASE_ID_RE.fullmatch(self.case_id) is None
            or (self.case_id != expected_id)
        ):
            raise ValueError("MathNet pilot case id is stale")
        supplied = _validate_digest(self.content_digest, "MathNet pilot row digest")
        expected = _digest(self._private_payload(), domain=_GOLD_FREE_ROW_DOMAIN)
        if not hmac.compare_digest(supplied, expected):
            raise ValueError("MathNet pilot row digest is stale")

    def selection_record(self) -> dict[str, object]:
        self.validate_current()
        return {
            "blind_gold_audit_required": self.blind_gold_audit_required,
            "case_id": self.case_id,
            "event_family_digest": self.event_family_digest,
            "problem_text_digest": self.problem_text_digest,
            "raw_language_state": self.raw_language_state.value,
            "rights_state": self.rights_state,
            "selection_coordinate_digest": self.selection_coordinate_digest,
        }

    def language_review_view(self) -> dict[str, str]:
        self.validate_current()
        raise RuntimeError(
            "direct row views expose the selection coordinate; use the replay-final "
            "language-audit projection builder"
        )


@dataclass(frozen=True, slots=True)
class MathNetPilotVaultEntryV3:
    selection_coordinate_digest: str
    source_row_commitment: str
    gold: MathNetPrivateGoldV3 = field(repr=False)
    content_digest: str = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if type(self) is not MathNetPilotVaultEntryV3:
            raise ValueError("MathNet pilot vault-entry subclasses are forbidden")
        self._validate_fields()
        object.__setattr__(
            self,
            "content_digest",
            _digest(self._private_payload(), domain=_VAULT_ENTRY_DOMAIN),
        )

    def _validate_fields(self) -> None:
        _validate_digest(
            self.selection_coordinate_digest,
            "MathNet pilot vault coordinate digest",
        )
        _validate_digest(
            self.source_row_commitment,
            "MathNet pilot source-row commitment",
        )
        if type(self.gold) is not MathNetPrivateGoldV3:
            raise ValueError("MathNet pilot vault entry requires exact private gold")
        self.gold.validate_current()

    def _private_payload(self) -> dict[str, object]:
        return {
            "gold": self.gold._private_dict(),
            "selection_coordinate_digest": self.selection_coordinate_digest,
            "source_row_commitment": self.source_row_commitment,
        }

    def validate_current(self) -> None:
        if type(self) is not MathNetPilotVaultEntryV3:
            raise ValueError("MathNet pilot vault-entry subclasses are forbidden")
        self._validate_fields()
        supplied = _validate_digest(
            self.content_digest,
            "MathNet pilot vault-entry digest",
        )
        expected = _digest(self._private_payload(), domain=_VAULT_ENTRY_DOMAIN)
        if not hmac.compare_digest(supplied, expected):
            raise ValueError("MathNet pilot vault-entry digest is stale")


def _initial_public_source_rows_state_v3(
    source_binding: MathNetSourceBindingV3,
) -> bytes:
    return hashlib.sha256(
        _PUBLIC_ROWS_CHAIN_DOMAIN + source_binding.content_digest.encode("ascii")
    ).digest()


def _advance_public_source_rows_state_v3(
    state: bytes,
    *,
    source_ordinal: int,
    row_commitment: str,
) -> bytes:
    _validate_digest(row_commitment, "MathNet pilot public source-row commitment")
    return hashlib.sha256(
        _PUBLIC_ROWS_CHAIN_DOMAIN
        + state
        + source_ordinal.to_bytes(8, "big")
        + row_commitment.encode("ascii")
    ).digest()


def _event_family_digest_v3(*, country: str, competition: str) -> str:
    return _digest(
        {"competition": competition, "country": country},
        domain=_EVENT_FAMILY_DOMAIN,
    )


def _optional_text_commitment_v3(value: str | None, *, label: str) -> str | None:
    if value is None:
        return None
    return _digest(value, domain=f"org.math-research-brain/mathnet-pilot-{label}/v3")


def _material_code_v3(
    rejection_code: MathNetEligibilityCodeV3 | None,
    *,
    raw_language: str | None,
) -> MathNetPilotMaterialCodeV3:
    if rejection_code is None:
        if raw_language == "English":
            return MathNetPilotMaterialCodeV3.SOURCE_ENGLISH_ELIGIBLE
        if raw_language is None:
            return MathNetPilotMaterialCodeV3.NULL_LANGUAGE_AUDIT_QUEUE
        raise ValueError("eligible MathNet pilot row has unsupported raw language")
    if rejection_code in {
        MathNetEligibilityCodeV3.ELIGIBLE,
        MathNetEligibilityCodeV3.LANGUAGE_AUDIT_REQUIRED,
    }:
        raise ValueError("MathNet pilot scalar screen returned an impossible code")
    try:
        return MathNetPilotMaterialCodeV3(rejection_code.value)
    except ValueError as exc:  # pragma: no cover - enum drift guard
        raise ValueError("MathNet pilot material-code mapping is incomplete") from exc


def _public_source_row_commitment_v3(
    *,
    source_binding_digest: str,
    source_ordinal: int,
    opaque_source_digest: str,
    problem_text_digest: str,
    event_family_digest: str | None,
    raw_language: str | None,
    problem_type: str | None,
    images_count: int,
    topics_flat: tuple[str, ...],
    material_code: MathNetPilotMaterialCodeV3,
) -> str:
    """Commit non-gold source state for every row without answer influence.

    Answer text, solution text, solution count, and all answer-derived digests are
    deliberately absent.  The material code may still change when a mutation crosses a
    frozen eligibility boundary; mutations that preserve the eligibility decision cannot
    perturb this gold-free source chain.
    """

    return _digest(
        {
            "event_family_digest": event_family_digest,
            "images_count": images_count,
            "material_code": material_code.value,
            "opaque_source_digest": opaque_source_digest,
            "problem_text_digest": problem_text_digest,
            "problem_type_commitment": _optional_text_commitment_v3(
                problem_type,
                label="problem-type",
            ),
            "raw_language_commitment": _optional_text_commitment_v3(
                raw_language,
                label="raw-language",
            ),
            "source_binding_digest": source_binding_digest,
            "source_ordinal": source_ordinal,
            "topics_commitment": _digest(
                [
                    _digest(
                        topic,
                        domain="org.math-research-brain/mathnet-pilot-topic/v3",
                    )
                    for topic in topics_flat
                ],
                domain="org.math-research-brain/mathnet-pilot-topics/v3",
            ),
        },
        domain=_PUBLIC_SOURCE_ROW_DOMAIN,
    )


def _screen_counts_tuple_v3(
    counts: Counter[MathNetPilotMaterialCodeV3],
) -> tuple[tuple[str, int], ...]:
    return tuple((code.value, counts[code]) for code in MathNetPilotMaterialCodeV3)


_UNIVERSE_TOKEN_V3 = object()


@dataclass(frozen=True, slots=True, init=False)
class GoldFreeMathNetPilotUniverseV3:
    """Gold-free full-source screen result; safe input to later audit preparation."""

    source_binding: MathNetSourceBindingV3
    screen_counts: tuple[tuple[str, int], ...]
    public_ordered_source_rows_digest: str
    gold_free_ordered_rows_digest: str
    coordinate_universe_digest: str
    rows: tuple[GoldFreeMathNetPilotRowV3, ...] = field(repr=False)
    content_digest: str = field(init=False)

    def __new__(cls, token: object = None) -> GoldFreeMathNetPilotUniverseV3:
        if token is not _UNIVERSE_TOKEN_V3:
            raise TypeError("MathNet pilot universes require the full-source builder")
        return object.__new__(cls)

    @classmethod
    def _derive(
        cls,
        *,
        source_binding: MathNetSourceBindingV3,
        screen_counts: tuple[tuple[str, int], ...],
        public_ordered_source_rows_digest: str,
        rows: tuple[GoldFreeMathNetPilotRowV3, ...],
    ) -> GoldFreeMathNetPilotUniverseV3:
        instance = cls(_UNIVERSE_TOKEN_V3)
        object.__setattr__(instance, "source_binding", source_binding)
        object.__setattr__(instance, "screen_counts", screen_counts)
        object.__setattr__(
            instance,
            "public_ordered_source_rows_digest",
            public_ordered_source_rows_digest,
        )
        object.__setattr__(instance, "rows", rows)
        object.__setattr__(
            instance,
            "gold_free_ordered_rows_digest",
            _digest(
                [
                    _digest(
                        row.selection_record(),
                        domain=_GOLD_FREE_PUBLIC_ROW_DOMAIN,
                    )
                    for row in rows
                ],
                domain="org.math-research-brain/mathnet-pilot-ordered-public-rows/v3",
            ),
        )
        object.__setattr__(
            instance,
            "coordinate_universe_digest",
            _digest(
                [row.selection_coordinate_digest for row in rows],
                domain=_COORDINATE_UNIVERSE_DOMAIN,
            ),
        )
        instance._validate_fields()
        object.__setattr__(
            instance,
            "content_digest",
            _digest(instance._private_payload(), domain=_UNIVERSE_DOMAIN),
        )
        return instance

    def __post_init__(self) -> None:  # pragma: no cover - init is deliberately disabled
        raise TypeError("MathNet pilot universes require the full-source builder")

    def _validate_fields(self) -> None:
        if type(self) is not GoldFreeMathNetPilotUniverseV3:
            raise ValueError("MathNet pilot universe subclasses are forbidden")
        if type(self.source_binding) is not MathNetSourceBindingV3:
            raise ValueError("MathNet pilot universe requires an exact source binding")
        self.source_binding.validate_current()
        expected_count_keys = tuple(code.value for code in MathNetPilotMaterialCodeV3)
        if (
            type(self.screen_counts) is not tuple
            or tuple(key for key, _value in self.screen_counts) != expected_count_keys
        ):
            raise ValueError("MathNet pilot screen counts are not canonical")
        if not all(
            type(item) is tuple
            and len(item) == 2
            and type(item[0]) is str
            and type(item[1]) is int
            and item[1] >= 0
            for item in self.screen_counts
        ):
            raise ValueError("MathNet pilot screen counts must be exact nonnegative integers")
        counts = dict(self.screen_counts)
        if sum(counts.values()) != self.source_binding.row_count:
            raise ValueError("MathNet pilot screen counts do not cover the pinned source")
        eligible_count = counts[MathNetPilotMaterialCodeV3.SOURCE_ENGLISH_ELIGIBLE.value]
        eligible_count += counts[MathNetPilotMaterialCodeV3.NULL_LANGUAGE_AUDIT_QUEUE.value]
        if type(self.rows) is not tuple or len(self.rows) != eligible_count:
            raise ValueError("MathNet pilot universe row count differs from its screens")
        for label, value in (
            ("public source-row chain", self.public_ordered_source_rows_digest),
            ("ordered gold-free rows", self.gold_free_ordered_rows_digest),
            ("coordinate universe", self.coordinate_universe_digest),
        ):
            _validate_digest(value, f"MathNet pilot {label} digest")
        coordinates: list[str] = []
        ordinals: list[int] = []
        language_counts: Counter[MathNetPilotLanguageStateV3] = Counter()
        for row in self.rows:
            if type(row) is not GoldFreeMathNetPilotRowV3:
                raise ValueError("MathNet pilot universe rows must be exact")
            row.validate_current()
            if row.source_binding_digest != self.source_binding.content_digest:
                raise ValueError("MathNet pilot row and source binding differ")
            coordinates.append(row.selection_coordinate_digest)
            ordinals.append(row.source_ordinal)
            language_counts[row.raw_language_state] += 1
        if coordinates != sorted(coordinates) or len(set(coordinates)) != len(coordinates):
            raise ValueError("MathNet pilot coordinates must be unique and sorted")
        if len(set(ordinals)) != len(ordinals):
            raise ValueError("MathNet pilot eligible source ordinals must be unique")
        if (
            language_counts[MathNetPilotLanguageStateV3.SOURCE_ENGLISH]
            != counts[MathNetPilotMaterialCodeV3.SOURCE_ENGLISH_ELIGIBLE.value]
            or language_counts[MathNetPilotLanguageStateV3.NULL_AUDIT_REQUIRED]
            != counts[MathNetPilotMaterialCodeV3.NULL_LANGUAGE_AUDIT_QUEUE.value]
        ):
            raise ValueError("MathNet pilot row language states differ from screen counts")
        expected_rows_digest = _digest(
            [
                _digest(row.selection_record(), domain=_GOLD_FREE_PUBLIC_ROW_DOMAIN)
                for row in self.rows
            ],
            domain="org.math-research-brain/mathnet-pilot-ordered-public-rows/v3",
        )
        if not hmac.compare_digest(
            self.gold_free_ordered_rows_digest,
            expected_rows_digest,
        ):
            raise ValueError("MathNet pilot ordered gold-free rows digest is stale")
        expected_coordinates = _digest(
            coordinates,
            domain=_COORDINATE_UNIVERSE_DOMAIN,
        )
        if not hmac.compare_digest(
            self.coordinate_universe_digest,
            expected_coordinates,
        ):
            raise ValueError("MathNet pilot coordinate-universe digest is stale")

    def _private_payload(self) -> dict[str, object]:
        return {
            "coordinate_universe_digest": self.coordinate_universe_digest,
            "gold_free_ordered_rows_digest": self.gold_free_ordered_rows_digest,
            "ordered_private_row_digests": [row.content_digest for row in self.rows],
            "public_ordered_source_rows_digest": (self.public_ordered_source_rows_digest),
            "screen_counts": [list(item) for item in self.screen_counts],
            "source_binding_digest": self.source_binding.content_digest,
        }

    def validate_current(self) -> None:
        self._validate_fields()
        supplied = _validate_digest(self.content_digest, "MathNet pilot universe digest")
        expected = _digest(self._private_payload(), domain=_UNIVERSE_DOMAIN)
        if not hmac.compare_digest(supplied, expected):
            raise ValueError("MathNet pilot universe digest is stale")

    def public_commitment_payload(self) -> dict[str, object]:
        self.validate_current()
        payload = {
            "candidate_count": len(self.rows),
            "coordinate_universe_digest": self.coordinate_universe_digest,
            "engineering_pilot_only": True,
            "gold_free_ordered_rows_digest": self.gold_free_ordered_rows_digest,
            "membership_annotation_scoreability_gated": True,
            "membership_chronology_attested": False,
            "private_gold_present": False,
            "public_ordered_source_rows_digest": (self.public_ordered_source_rows_digest),
            "schema": MATHNET_PILOT_MATERIAL_SCHEMA_V3,
            "schema_version": MATHNET_PILOT_MATERIAL_VERSION_V3,
            "screen_counts": [list(item) for item in self.screen_counts],
            "source_binding": self.source_binding.to_dict(),
            "universe_content_digest": self.content_digest,
        }
        return payload | {"public_content_digest": _digest(payload, domain=_UNIVERSE_PUBLIC_DOMAIN)}


_VAULT_TOKEN_V3 = object()


@dataclass(frozen=True, slots=True, init=False)
class MathNetPilotGoldVaultV3:
    """Private source/gold evidence with a non-authenticating keyed commitment."""

    source_binding_digest: str
    bound_universe_content_digest: str
    entries: tuple[MathNetPilotVaultEntryV3, ...] = field(repr=False)
    private_ordered_source_rows_digest: str = field(repr=False)
    vault_key_commitment: str
    public_hmac_commitment: str
    private_content_digest: str = field(repr=False)
    content_digest: str

    def __new__(cls, token: object = None) -> MathNetPilotGoldVaultV3:
        if token is not _VAULT_TOKEN_V3:
            raise TypeError("MathNet pilot vaults require the private full-source builder")
        return object.__new__(cls)

    @classmethod
    def _derive(
        cls,
        *,
        source_binding_digest: str,
        bound_universe_content_digest: str,
        entries: tuple[MathNetPilotVaultEntryV3, ...],
        private_ordered_source_rows_digest: str,
        vault_key: bytes,
    ) -> MathNetPilotGoldVaultV3:
        key = _validate_vault_key(vault_key)
        instance = cls(_VAULT_TOKEN_V3)
        object.__setattr__(instance, "source_binding_digest", source_binding_digest)
        object.__setattr__(
            instance,
            "bound_universe_content_digest",
            bound_universe_content_digest,
        )
        object.__setattr__(instance, "entries", entries)
        object.__setattr__(
            instance,
            "private_ordered_source_rows_digest",
            private_ordered_source_rows_digest,
        )
        private_content_digest = _digest(
            instance._private_payload(),
            domain=_VAULT_PRIVATE_DOMAIN,
        )
        object.__setattr__(
            instance,
            "private_content_digest",
            private_content_digest,
        )
        object.__setattr__(
            instance,
            "vault_key_commitment",
            "sha256:" + hashlib.sha256(_VAULT_KEY_DOMAIN + key).hexdigest(),
        )
        object.__setattr__(
            instance,
            "public_hmac_commitment",
            "hmac-sha256:"
            + hmac.new(
                key,
                _VAULT_HMAC_DOMAIN + private_content_digest.encode("ascii"),
                hashlib.sha256,
            ).hexdigest(),
        )
        instance._validate_fields()
        object.__setattr__(
            instance,
            "content_digest",
            _digest(instance._public_payload(), domain=_VAULT_PUBLIC_DOMAIN),
        )
        return instance

    def __post_init__(self) -> None:  # pragma: no cover - init is deliberately disabled
        raise TypeError("MathNet pilot vaults require the private full-source builder")

    def __reduce_ex__(self, protocol: int) -> object:
        del protocol
        raise TypeError("MathNet pilot private vaults cannot cross a pickle boundary")

    def _validate_fields(self) -> None:
        if type(self) is not MathNetPilotGoldVaultV3:
            raise ValueError("MathNet pilot vault subclasses are forbidden")
        _validate_digest(self.source_binding_digest, "MathNet pilot vault source binding")
        _validate_digest(
            self.bound_universe_content_digest,
            "MathNet pilot vault-bound universe",
        )
        _validate_digest(
            self.private_ordered_source_rows_digest,
            "MathNet pilot private source-row chain",
        )
        _validate_digest(self.vault_key_commitment, "MathNet pilot vault-key commitment")
        _validate_hmac(self.public_hmac_commitment, "MathNet pilot vault HMAC")
        if type(self.entries) is not tuple:
            raise ValueError("MathNet pilot vault entries must be an exact tuple")
        coordinates: list[str] = []
        source_row_commitments: list[str] = []
        for entry in self.entries:
            if type(entry) is not MathNetPilotVaultEntryV3:
                raise ValueError("MathNet pilot vault entries must be exact")
            entry.validate_current()
            coordinates.append(entry.selection_coordinate_digest)
            source_row_commitments.append(entry.source_row_commitment)
        if coordinates != sorted(coordinates) or len(set(coordinates)) != len(coordinates):
            raise ValueError("MathNet pilot vault coordinates must be unique and sorted")
        if len(set(source_row_commitments)) != len(source_row_commitments):
            raise ValueError("MathNet pilot vault source-row commitments must be unique")

    def _private_payload(self) -> dict[str, object]:
        return {
            "bound_universe_content_digest": self.bound_universe_content_digest,
            "ordered_entry_digests": [entry.content_digest for entry in self.entries],
            "private_ordered_source_rows_digest": (self.private_ordered_source_rows_digest),
            "source_binding_digest": self.source_binding_digest,
        }

    def _public_payload(self) -> dict[str, object]:
        return {
            "blind_gold_audit_required": True,
            "bound_universe_content_digest": self.bound_universe_content_digest,
            "chronology_attested": False,
            "entry_count": len(self.entries),
            "hmac_holder_identity_attested": False,
            "private_gold_audit_authority": False,
            "public_hmac_commitment": self.public_hmac_commitment,
            "source_binding_digest": self.source_binding_digest,
            "vault_key_commitment": self.vault_key_commitment,
        }

    def validate_current(self) -> None:
        self._validate_fields()
        supplied_private = _validate_digest(
            self.private_content_digest,
            "MathNet pilot private vault-content digest",
        )
        expected_private = _digest(
            self._private_payload(),
            domain=_VAULT_PRIVATE_DOMAIN,
        )
        if not hmac.compare_digest(supplied_private, expected_private):
            raise ValueError("MathNet pilot private vault-content digest is stale")
        supplied_public = _validate_digest(
            self.content_digest,
            "MathNet pilot public vault digest",
        )
        expected_public = _digest(self._public_payload(), domain=_VAULT_PUBLIC_DOMAIN)
        if not hmac.compare_digest(supplied_public, expected_public):
            raise ValueError("MathNet pilot public vault digest is stale")

    def verify_key(self, vault_key: bytes) -> None:
        self.validate_current()
        key = _validate_vault_key(vault_key)
        expected_key = "sha256:" + hashlib.sha256(_VAULT_KEY_DOMAIN + key).hexdigest()
        if not hmac.compare_digest(self.vault_key_commitment, expected_key):
            raise ValueError("MathNet pilot vault key differs from its commitment")
        expected_hmac = (
            "hmac-sha256:"
            + hmac.new(
                key,
                _VAULT_HMAC_DOMAIN + self.private_content_digest.encode("ascii"),
                hashlib.sha256,
            ).hexdigest()
        )
        if not hmac.compare_digest(self.public_hmac_commitment, expected_hmac):
            raise ValueError("MathNet pilot vault HMAC is stale")

    def public_commitment_payload(self) -> dict[str, object]:
        self.validate_current()
        return self._public_payload() | {"content_digest": self.content_digest}


_MATERIAL_TOKEN_V3 = object()


@dataclass(frozen=True, slots=True, init=False)
class MathNetPilotMaterialV3:
    """Coupled gold-free universe and private vault; never pass this to a selector."""

    universe: GoldFreeMathNetPilotUniverseV3
    vault: MathNetPilotGoldVaultV3 = field(repr=False)
    private_content_digest: str = field(repr=False)
    content_digest: str

    def __new__(cls, token: object = None) -> MathNetPilotMaterialV3:
        if token is not _MATERIAL_TOKEN_V3:
            raise TypeError("MathNet pilot material requires the full-source builder")
        return object.__new__(cls)

    @classmethod
    def _derive(
        cls,
        *,
        universe: GoldFreeMathNetPilotUniverseV3,
        vault: MathNetPilotGoldVaultV3,
    ) -> MathNetPilotMaterialV3:
        instance = cls(_MATERIAL_TOKEN_V3)
        object.__setattr__(instance, "universe", universe)
        object.__setattr__(instance, "vault", vault)
        instance._validate_fields()
        object.__setattr__(
            instance,
            "private_content_digest",
            _digest(instance._private_payload(), domain=_MATERIAL_DOMAIN),
        )
        object.__setattr__(
            instance,
            "content_digest",
            _digest(instance._public_payload(), domain=_MATERIAL_PUBLIC_DOMAIN),
        )
        return instance

    def __post_init__(self) -> None:  # pragma: no cover - init is deliberately disabled
        raise TypeError("MathNet pilot material requires the full-source builder")

    def __reduce_ex__(self, protocol: int) -> object:
        del protocol
        raise TypeError("combined MathNet pilot material cannot cross a pickle boundary")

    def _validate_fields(self) -> None:
        if type(self) is not MathNetPilotMaterialV3:
            raise ValueError("MathNet pilot material subclasses are forbidden")
        if type(self.universe) is not GoldFreeMathNetPilotUniverseV3:
            raise ValueError("MathNet pilot material requires an exact universe")
        if type(self.vault) is not MathNetPilotGoldVaultV3:
            raise ValueError("MathNet pilot material requires an exact vault")
        self.universe.validate_current()
        self.vault.validate_current()
        if self.universe.source_binding.content_digest != self.vault.source_binding_digest:
            raise ValueError("MathNet pilot universe and vault source bindings differ")
        if not hmac.compare_digest(
            self.universe.content_digest,
            self.vault.bound_universe_content_digest,
        ):
            raise ValueError("MathNet pilot vault is not bound to this universe")
        universe_coordinates = tuple(row.selection_coordinate_digest for row in self.universe.rows)
        vault_coordinates = tuple(entry.selection_coordinate_digest for entry in self.vault.entries)
        if universe_coordinates != vault_coordinates:
            raise ValueError("MathNet pilot universe and vault coordinates differ")

    def _private_payload(self) -> dict[str, object]:
        return {
            "universe_content_digest": self.universe.content_digest,
            "vault_private_content_digest": self.vault.private_content_digest,
        }

    def _public_payload(self) -> dict[str, object]:
        return {
            "engineering_pilot_only": True,
            "lookup_blinding_attested": False,
            "runtime_isolation_manifest_required": True,
            "schema": MATHNET_PILOT_MATERIAL_SCHEMA_V3,
            "schema_version": MATHNET_PILOT_MATERIAL_VERSION_V3,
            "selector_handoff_authorized": False,
            "universe": self.universe.public_commitment_payload(),
            "vault": self.vault.public_commitment_payload(),
        }

    def validate_current(self) -> None:
        self._validate_fields()
        supplied_private = _validate_digest(
            self.private_content_digest,
            "MathNet pilot private material digest",
        )
        expected_private = _digest(self._private_payload(), domain=_MATERIAL_DOMAIN)
        if not hmac.compare_digest(supplied_private, expected_private):
            raise ValueError("MathNet pilot private material digest is stale")
        supplied_public = _validate_digest(
            self.content_digest,
            "MathNet pilot public material digest",
        )
        expected_public = _digest(
            self._public_payload(),
            domain=_MATERIAL_PUBLIC_DOMAIN,
        )
        if not hmac.compare_digest(supplied_public, expected_public):
            raise ValueError("MathNet pilot public material digest is stale")

    def public_commitment_payload(self) -> dict[str, object]:
        self.validate_current()
        return self._public_payload() | {"content_digest": self.content_digest}


def _build_mathnet_pilot_material_v3(
    rows: Iterable[object],
    *,
    source_binding: MathNetSourceBindingV3,
    vault_key: bytes,
) -> MathNetPilotMaterialV3:
    """Build material from one exact canonical-order pass over every pinned row."""

    if type(source_binding) is not MathNetSourceBindingV3:
        raise ValueError("MathNet pilot material requires an exact source binding")
    source_binding.validate_current()
    key = _validate_vault_key(vault_key)
    if not isinstance(rows, Iterable) or isinstance(rows, (str, bytes, bytearray, dict)):
        raise ValueError("MathNet pilot source rows must be a non-string iterable")

    counts: Counter[MathNetPilotMaterialCodeV3] = Counter()
    gold_free_rows: list[GoldFreeMathNetPilotRowV3] = []
    vault_entries: list[MathNetPilotVaultEntryV3] = []
    seen_source_ids: set[str] = set()
    private_rows_state = _initial_source_rows_state_v3(source_binding)
    public_rows_state = _initial_public_source_rows_state_v3(source_binding)
    observed_count = 0

    for source_ordinal, raw in enumerate(rows):
        if source_ordinal >= source_binding.row_count:
            raise ValueError("MathNet pilot source contains rows beyond the pinned count")
        fields = _read_mathnet_row_v3(raw)
        if fields.source_id in seen_source_ids:
            raise ValueError("MathNet pilot source IDs must be globally unique")
        seen_source_ids.add(fields.source_id)
        observed_count += 1

        source_row_commitment = _source_row_commitment_v3(
            fields,
            source_binding=source_binding,
            source_ordinal=source_ordinal,
        )
        private_rows_state = _advance_source_rows_state_v3(
            private_rows_state,
            source_ordinal=source_ordinal,
            row_commitment=source_row_commitment,
        )
        screen = _screen_mathnet_row_fields_for_pilot_v3(
            fields,
            permit_null_language_audit=True,
        )
        material_code = _material_code_v3(
            screen.rejection_code,
            raw_language=fields.language,
        )
        counts[material_code] += 1
        opaque_source_digest = _opaque_source_digest_v3(
            source_binding=source_binding,
            source_ordinal=source_ordinal,
            source_id=fields.source_id,
        )
        problem_text_digest = _text_digest(fields.problem_markdown)
        event_family_digest = (
            None
            if fields.country is None or fields.competition is None
            else _event_family_digest_v3(
                country=fields.country,
                competition=fields.competition,
            )
        )
        public_row_commitment = _public_source_row_commitment_v3(
            source_binding_digest=source_binding.content_digest,
            source_ordinal=source_ordinal,
            opaque_source_digest=opaque_source_digest,
            problem_text_digest=problem_text_digest,
            event_family_digest=event_family_digest,
            raw_language=fields.language,
            problem_type=fields.problem_type,
            images_count=fields.images_count,
            topics_flat=fields.topics_flat,
            material_code=material_code,
        )
        public_rows_state = _advance_public_source_rows_state_v3(
            public_rows_state,
            source_ordinal=source_ordinal,
            row_commitment=public_row_commitment,
        )

        if screen.rejection_code is not None:
            continue
        if (
            fields.country is None
            or fields.competition is None
            or event_family_digest is None
            or type(screen.gold) is not MathNetPrivateGoldV3
        ):
            raise ValueError("eligible MathNet pilot row lost exact source or gold state")
        language_state = (
            MathNetPilotLanguageStateV3.SOURCE_ENGLISH
            if fields.language == "English"
            else MathNetPilotLanguageStateV3.NULL_AUDIT_REQUIRED
        )
        coordinate_payload = {
            "event_family_digest": event_family_digest,
            "opaque_source_digest": opaque_source_digest,
            "problem_text_digest": problem_text_digest,
            "raw_language_state": language_state.value,
            "source_binding_digest": source_binding.content_digest,
            "source_ordinal": source_ordinal,
        }
        coordinate_digest = _digest(
            coordinate_payload,
            domain=_ROW_COORDINATE_DOMAIN,
        )
        gold_free_rows.append(
            GoldFreeMathNetPilotRowV3(
                source_binding_digest=source_binding.content_digest,
                source_ordinal=source_ordinal,
                opaque_source_digest=opaque_source_digest,
                problem_text=fields.problem_markdown,
                problem_text_digest=problem_text_digest,
                event_family_digest=event_family_digest,
                raw_language_state=language_state,
                selection_coordinate_digest=coordinate_digest,
            )
        )
        vault_entries.append(
            MathNetPilotVaultEntryV3(
                selection_coordinate_digest=coordinate_digest,
                source_row_commitment=source_row_commitment,
                gold=screen.gold,
            )
        )

    if observed_count != source_binding.row_count:
        raise ValueError("MathNet pilot source row count differs from the pinned release")

    gold_free_rows.sort(key=lambda row: row.selection_coordinate_digest)
    vault_entries.sort(key=lambda entry: entry.selection_coordinate_digest)
    universe = GoldFreeMathNetPilotUniverseV3._derive(
        source_binding=source_binding,
        screen_counts=_screen_counts_tuple_v3(counts),
        public_ordered_source_rows_digest="sha256:" + public_rows_state.hex(),
        rows=tuple(gold_free_rows),
    )
    vault = MathNetPilotGoldVaultV3._derive(
        source_binding_digest=source_binding.content_digest,
        bound_universe_content_digest=universe.content_digest,
        entries=tuple(vault_entries),
        private_ordered_source_rows_digest="sha256:" + private_rows_state.hex(),
        vault_key=key,
    )
    material = MathNetPilotMaterialV3._derive(universe=universe, vault=vault)
    material.vault.verify_key(key)
    material.validate_current()
    return material


def build_synthetic_mathnet_pilot_material_v3(
    rows: Iterable[object],
    *,
    vault_key: bytes,
) -> MathNetPilotMaterialV3:
    """Build explicitly synthetic test material; this confers no official authority."""

    return _build_mathnet_pilot_material_v3(
        rows,
        source_binding=synthetic_mathnet_source_binding_v3(),
        vault_key=vault_key,
    )


def _verify_mathnet_pilot_material_v3(
    material: MathNetPilotMaterialV3,
    rows: Iterable[object],
    *,
    source_binding: MathNetSourceBindingV3,
    vault_key: bytes,
) -> None:
    if type(material) is not MathNetPilotMaterialV3:
        raise ValueError("MathNet pilot verification requires exact material")
    material.validate_current()
    material.vault.verify_key(vault_key)
    rebuilt = _build_mathnet_pilot_material_v3(
        rows,
        source_binding=source_binding,
        vault_key=vault_key,
    )
    if not hmac.compare_digest(
        material.private_content_digest,
        rebuilt.private_content_digest,
    ):
        raise ValueError("MathNet pilot material differs from the supplied source rows")
    if material != rebuilt:
        raise ValueError("MathNet pilot material object differs from deterministic replay")


def verify_synthetic_mathnet_pilot_material_v3(
    material: MathNetPilotMaterialV3,
    rows: Iterable[object],
    *,
    vault_key: bytes,
) -> None:
    """Replay synthetic rows and the private key against one material artifact."""

    _verify_mathnet_pilot_material_v3(
        material,
        rows,
        source_binding=synthetic_mathnet_source_binding_v3(),
        vault_key=vault_key,
    )
