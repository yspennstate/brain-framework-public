"""Append-only, outcome-blind pilot for the fixed public hard-v2 challenges.

The pilot deliberately keeps three boundaries separate:

* a small public preregistration exposes policy digests and aggregate balance only;
* a caller-held private state retains the HMAC keys and exact dispatch material;
* an :class:`~math_brain.attempt_ledger.AttemptLedger` burns each selected slot once.

The selected 64-case sample is fixed in source and never used by the smoke path.  A
successful, eligible nonselected smoke artifact is required before execution can burn
the first selected slot.  Scoring reloads only durable ledger artifacts and counts all
64 precommitted slots in the denominator, including every infrastructure failure.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import stat
from collections import Counter
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from . import hard_calibration_v3 as calibration_bridge
from . import hard_challenge_v2 as challenge_module
from .attempt_ledger import (
    ArtifactPayload,
    AttemptLedger,
    AttemptRunnerFailed,
    DispatchMaterial,
    LedgerIntegrityError,
    OutcomeKind,
    SlotCommitment,
    SlotState,
    new_slot_id,
)
from .codex_artifact_io import (
    CodexArtifactError,
    deserialize_codex_run_result,
    serialize_codex_run_result,
)
from .codex_runner import (
    MAX_AUTH_FILE_BYTES,
    CodexAppServerRunner,
    CodexRunConfig,
    CodexRunResult,
    ExecutableProvenance,
    app_server_command_tail_policy_digest,
    capture_codex_executable_provenance,
    solver_process_isolation_policy_digest,
)
from .hard_calibration_v3 import HardCalibrationPreregistrationV3
from .hard_challenge_v2 import (
    ANSWER_SCHEMA_V2,
    ANSWER_SCHEMA_VERSION_V2,
    DERIVATION_FAMILY_ORDER_V2,
    FAMILY_ORDER_V2,
    FAULT_MECHANISM_ORDER_V2,
    MAX_INTEGER_ABS_V2,
    PARAMETER_CELL_ORDER_V2,
    HardChallengeCaseV2,
    SolverOutputV2,
    UniversalWitnessCodeV2,
)

HARD_BLIND_PILOT_SCHEMA_V1 = "org.math-research-brain/hard-blind-pilot-preregistration"
HARD_BLIND_PILOT_SCHEMA_VERSION_V1 = 1
HARD_BLIND_PRIVATE_STATE_SCHEMA_V1 = "org.math-research-brain/hard-blind-pilot-private-state"
HARD_BLIND_PRIVATE_STATE_SCHEMA_VERSION_V1 = 1
HARD_BLIND_PRIVATE_ARTIFACT_MEDIA_TYPE_V1 = (
    "application/vnd.math-research-brain.private-hard-blind-pilot-codex-result"
)
HARD_BLIND_PILOT_CASE_COUNT_V1 = 64
HARD_BLIND_PILOT_REQUESTED_MODEL_V1 = "gpt-5.6-sol"
HARD_BLIND_PILOT_REQUESTED_PROVIDER_V1 = "openai"
HARD_BLIND_PILOT_REQUESTED_EFFORT_V1 = "ultra"
HARD_BLIND_PILOT_TIMEOUT_SECONDS_V1 = 180
HARD_BLIND_PILOT_MAX_WORKERS_V1 = 1
HARD_BLIND_PILOT_TARGET_MIN_V1 = 0.45
HARD_BLIND_PILOT_TARGET_MAX_V1 = 0.75
HARD_BLIND_PILOT_REJECT_BELOW_V1 = 0.30
HARD_BLIND_PILOT_REJECT_ABOVE_V1 = 0.85
# A solver-view-only prompt/trace join recovered both the fault line and
# mechanism on all 320 public-development cases without mathematical replay.
# The published zero-start pilot is therefore retained only as an audit
# artifact; selected collection must fail closed until a new generator and
# preregistration remove that shortcut.
HARD_BLIND_PILOT_COLLECTION_ENABLED_V1 = False
HARD_BLIND_PILOT_COLLECTION_BLOCKER_V1 = (
    "perfect solver-visible relational shortcut on the public-development bank"
)
HARD_BLIND_PRIVATE_STATE_MAX_BYTES_V1 = 64_000
HARD_BLIND_RUN_MAX_TRANSCRIPT_BYTES_V1 = 8_000_000

HARD_BLIND_PILOT_BASE_INSTRUCTIONS_V1 = (
    "You are an independent mathematical derivation auditor. Work only from the "
    "user-supplied problem and numbered derivation. Return only the JSON object "
    "required by the supplied output schema."
)
HARD_BLIND_PILOT_DEVELOPER_INSTRUCTIONS_V1 = (
    "Do not use tools, files, network access, plugins, skills, memory, subagents, or "
    "ambient workspace context. Do not ask questions or reveal chain-of-thought."
)
HARD_BLIND_PILOT_SOLVER_PREFIX_V1 = (
    "Audit this derivation independently. The derivation contains exactly one invalid "
    "interior transition. Identify its first invalid line, then give the normalized "
    "repair and diagnostic witness required by the problem's universal answer contract. "
    "Return one JSON object and no surrounding prose."
)

# This order was fixed outcome-blind.  The historical digest is deliberately the SHA-256
# of UTF-8 IDs joined by one LF and *without* a final LF.  A domain-separated commitment
# is used in the public payload; retaining the historical value prevents an accidental
# switch to canonical-JSON hashing from masquerading as the same sample.
HARD_BLIND_PILOT_ORIGINAL_CASE_IDS_V1 = (
    "DV2-CRT-00000850",
    "DV2-CRT-00000219",
    "DV2-CRT-00000622",
    "DV2-CRT-0000047b",
    "DV2-CRT-0000092c",
    "DV2-CRT-0000004d",
    "DV2-CRT-00000966",
    "DV2-CRT-0000036f",
    "DV2-CRT-00000580",
    "DV2-CRT-000001b1",
    "DV2-CRT-000001a2",
    "DV2-CRT-000004b3",
    "DV2-CRT-000002dc",
    "DV2-CRT-000002cd",
    "DV2-CRT-00000596",
    "DV2-CRT-000007d7",
    "DV2-COM-00000e68",
    "DV2-COM-00001211",
    "DV2-COM-00001162",
    "DV2-COM-00000e33",
    "DV2-COM-00001044",
    "DV2-COM-00000a1d",
    "DV2-COM-00001206",
    "DV2-COM-00000c3f",
    "DV2-COM-000012c0",
    "DV2-COM-000013a9",
    "DV2-COM-00000be2",
    "DV2-COM-00000cf3",
    "DV2-COM-00000d94",
    "DV2-COM-00000d9d",
    "DV2-COM-00000ade",
    "DV2-COM-00000fcf",
    "DV2-SPT-00001630",
    "DV2-SPT-00001a09",
    "DV2-SPT-0000155a",
    "DV2-SPT-00001b6b",
    "DV2-SPT-0000184c",
    "DV2-SPT-00001c3d",
    "DV2-SPT-00001706",
    "DV2-SPT-00001d5f",
    "DV2-SPT-00001598",
    "DV2-SPT-00001d89",
    "DV2-SPT-000017ca",
    "DV2-SPT-00001d93",
    "DV2-SPT-00001cb4",
    "DV2-SPT-00001cf5",
    "DV2-SPT-000019f6",
    "DV2-SPT-00001adf",
    "DV2-REC-00001f08",
    "DV2-REC-00002479",
    "DV2-REC-00002332",
    "DV2-REC-0000204b",
    "DV2-REC-00002324",
    "DV2-REC-00002215",
    "DV2-REC-00002626",
    "DV2-REC-0000217f",
    "DV2-REC-000023e0",
    "DV2-REC-000023f9",
    "DV2-REC-0000208a",
    "DV2-REC-000023ab",
    "DV2-REC-000026e4",
    "DV2-REC-000027dd",
    "DV2-REC-000021a6",
    "DV2-REC-00001ee7",
)

_SAMPLE_BYTES_V1 = "\n".join(HARD_BLIND_PILOT_ORIGINAL_CASE_IDS_V1).encode("ascii")
HARD_BLIND_PILOT_HISTORICAL_SAMPLE_DIGEST_V1 = (
    "sha256:" + hashlib.sha256(_SAMPLE_BYTES_V1).hexdigest()
)
if HARD_BLIND_PILOT_HISTORICAL_SAMPLE_DIGEST_V1 != (
    "sha256:83b7d57feae92f046feaff6b5428f58861800bc996e33bc11a178d68690cef2a"
):  # pragma: no cover - import-time regression tripwire
    raise RuntimeError("fixed hard-blind sample order or newline hashing changed")
HARD_BLIND_PILOT_SAMPLE_COMMITMENT_V1 = (
    "sha256:"
    + hashlib.sha256(b"math-brain/hard-blind-pilot/sample/v1\x00" + _SAMPLE_BYTES_V1).hexdigest()
)

_PINNED_BUILD_PREREGISTRATION = calibration_bridge.build_hard_calibration_preregistration_v3
_PINNED_GENERATE_BANK = challenge_module.generate_public_calibration_bank_v2
_PINNED_CERTIFICATION = challenge_module.certification_report_v2
_PINNED_VERIFY_SOLVER_OUTPUT = HardChallengeCaseV2.verify_solver_output

_PUBLIC_TOP_LEVEL_KEYS_V1 = {
    "attempt_policy",
    "attempt_ledger_digest",
    "attempt_universe_commitment",
    "balance",
    "calibration_public_commitment_digest",
    "case_count",
    "model_identity_verified",
    "response_contract_digest",
    "run_policy",
    "run_spec_digest",
    "sample_commitment",
    "schema",
    "schema_version",
    "source_revision",
    "thresholds",
}
_PUBLIC_FORBIDDEN_KEY_FRAGMENTS_V1 = (
    "auth_file",
    "case_id",
    "dispatch",
    "executable_path",
    "ledger_key",
    "manifest",
    "original_id",
    "prompt",
    "rank",
    "selection_seed",
    "slot_id",
    "stable_id",
)


def _sha256(raw: bytes) -> str:
    if type(raw) is not bytes:
        raise ValueError("digest input must be exact bytes")
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _canonical_json_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError) as exc:
        raise ValueError("value is not canonical bounded JSON") from exc


def _digest(value: Any) -> str:
    return _sha256(_canonical_json_bytes(value))


def _require_digest(value: Any, label: str) -> str:
    if type(value) is not str or re.fullmatch(r"sha256:[0-9a-f]{64}", value) is None:
        raise ValueError(f"{label} must be canonical SHA-256")
    return value


def _require_revision(value: Any) -> str:
    if type(value) is not str or re.fullmatch(r"[0-9a-f]{40,64}", value) is None:
        raise ValueError("source revision must be a lowercase Git object id")
    return value


def _require_exact_text(value: Any, label: str, *, maximum_bytes: int = 512) -> str:
    if type(value) is not str or not value.strip():
        raise ValueError(f"{label} must be nonempty exact text")
    try:
        size = len(value.encode("utf-8"))
    except UnicodeEncodeError as exc:
        raise ValueError(f"{label} must be valid UTF-8") from exc
    if size > maximum_bytes:
        raise ValueError(f"{label} exceeds its byte budget")
    return value


def _strict_keys(value: Any, expected: set[str], label: str) -> dict[str, Any]:
    if type(value) is not dict or not all(type(key) is str for key in value):
        raise ValueError(f"{label} must be an exact object with string keys")
    if set(value) != expected:
        raise ValueError(f"{label} fields differ from the fixed schema")
    return value


def _normalized_value_schema_v1() -> dict[str, Any]:
    integer = {
        "additionalProperties": False,
        "properties": {
            "kind": {"const": "integer"},
            "payload": {
                "additionalProperties": False,
                "properties": {
                    "value": {
                        "maximum": MAX_INTEGER_ABS_V2,
                        "minimum": -MAX_INTEGER_ABS_V2,
                        "type": "integer",
                    }
                },
                "required": ["value"],
                "type": "object",
            },
        },
        "required": ["kind", "payload"],
        "type": "object",
    }
    rational = {
        "additionalProperties": False,
        "properties": {
            "kind": {"const": "rational"},
            "payload": {
                "additionalProperties": False,
                "properties": {
                    "denominator": {
                        "maximum": MAX_INTEGER_ABS_V2,
                        "minimum": 2,
                        "type": "integer",
                    },
                    "numerator": {
                        "maximum": MAX_INTEGER_ABS_V2,
                        "minimum": -MAX_INTEGER_ABS_V2,
                        "type": "integer",
                    },
                },
                "required": ["denominator", "numerator"],
                "type": "object",
            },
        },
        "required": ["kind", "payload"],
        "type": "object",
    }
    integer_vector = {
        "additionalProperties": False,
        "properties": {
            "kind": {"const": "integer_vector"},
            "payload": {
                "additionalProperties": False,
                "properties": {
                    "items": {
                        "items": {
                            "maximum": MAX_INTEGER_ABS_V2,
                            "minimum": -MAX_INTEGER_ABS_V2,
                            "type": "integer",
                        },
                        "maxItems": 16,
                        "minItems": 1,
                        "type": "array",
                    }
                },
                "required": ["items"],
                "type": "object",
            },
        },
        "required": ["kind", "payload"],
        "type": "object",
    }
    symbol_vector = {
        "additionalProperties": False,
        "properties": {
            "kind": {"const": "symbol_vector"},
            "payload": {
                "additionalProperties": False,
                "properties": {
                    "items": {
                        "items": {
                            "maxLength": 64,
                            "pattern": "^[A-Za-z][A-Za-z0-9_]*$",
                            "type": "string",
                        },
                        "maxItems": 16,
                        "minItems": 1,
                        "type": "array",
                    }
                },
                "required": ["items"],
                "type": "object",
            },
        },
        "required": ["kind", "payload"],
        "type": "object",
    }
    return {"oneOf": [integer, rational, integer_vector, symbol_vector]}


def hard_blind_solver_output_schema_v1() -> dict[str, Any]:
    """Return a fresh exact JSON schema for the universal ``SolverOutputV2`` view."""

    normalized = _normalized_value_schema_v1()
    witness_value = json.loads(_canonical_json_bytes(normalized))
    return {
        "additionalProperties": False,
        "properties": {
            "error_code": {"const": "invalid_transition"},
            "first_invalid_line": {"maximum": 11, "minimum": 2, "type": "integer"},
            "repair": {
                "additionalProperties": False,
                "properties": {
                    "target": {"const": "value"},
                    "value": normalized,
                },
                "required": ["target", "value"],
                "type": "object",
            },
            "schema": {"const": ANSWER_SCHEMA_V2},
            "schema_version": {"const": ANSWER_SCHEMA_VERSION_V2},
            "witness": {
                "additionalProperties": False,
                "properties": {
                    "code": {
                        "enum": [item.value for item in UniversalWitnessCodeV2],
                        "type": "string",
                    },
                    "values": {
                        "items": witness_value,
                        "maxItems": 8,
                        "minItems": 1,
                        "type": "array",
                    },
                },
                "required": ["code", "values"],
                "type": "object",
            },
        },
        "required": [
            "error_code",
            "first_invalid_line",
            "repair",
            "schema",
            "schema_version",
            "witness",
        ],
        "type": "object",
    }


def hard_blind_solver_output_schema_json_v1() -> bytes:
    return _canonical_json_bytes(hard_blind_solver_output_schema_v1())


def hard_blind_solver_prompt_v1(case: HardChallengeCaseV2) -> str:
    """Render one answer-free faulty solver view with an exact instruction prefix."""

    if type(case) is not HardChallengeCaseV2:
        raise ValueError("solver prompt requires an exact hard challenge")
    view = case.solver_view(faulty=True)
    prompt = (
        HARD_BLIND_PILOT_SOLVER_PREFIX_V1
        + "\n\nProblem\n"
        + view.prompt
        + "\n\nNumbered derivation\n"
        + "\n".join(view.numbered_solution)
    )
    if len(prompt.encode("utf-8")) > 32_768:
        raise ValueError("hard-blind solver prompt exceeds its byte budget")
    return prompt


def _expected_request_policy_material_v1(policy: HardBlindPilotRunPolicyV1) -> dict[str, Any]:
    return {
        "base_instructions": policy.base_instructions,
        "developer_instructions": policy.developer_instructions,
        "fixed_thread_policy": {
            "allow_provider_model_fallback": False,
            "approval_policy": "never",
            "dynamic_tools": [],
            "environments": [],
            "ephemeral": True,
            "experimental_raw_events": True,
            "sandbox": "read-only",
        },
        "fixed_turn_policy": {"approval_policy": "never", "environments": []},
        "requested_model": policy.requested_model,
        "requested_provider": policy.requested_provider,
        "requested_reasoning_effort": policy.requested_effort,
        "timeout_seconds": policy.timeout_seconds,
    }


@dataclass(frozen=True, slots=True)
class HardBlindPilotRunPolicyV1:
    """Exact source, executable, instruction, and process policy fixed before attempts."""

    source_revision: str
    executable_provenance: ExecutableProvenance
    requested_model: str = HARD_BLIND_PILOT_REQUESTED_MODEL_V1
    requested_provider: str = HARD_BLIND_PILOT_REQUESTED_PROVIDER_V1
    requested_effort: str = HARD_BLIND_PILOT_REQUESTED_EFFORT_V1
    timeout_seconds: int = HARD_BLIND_PILOT_TIMEOUT_SECONDS_V1
    max_workers: int = HARD_BLIND_PILOT_MAX_WORKERS_V1
    base_instructions: str = HARD_BLIND_PILOT_BASE_INSTRUCTIONS_V1
    developer_instructions: str = HARD_BLIND_PILOT_DEVELOPER_INSTRUCTIONS_V1

    def __post_init__(self) -> None:
        _require_revision(self.source_revision)
        if type(self.executable_provenance) is not ExecutableProvenance:
            raise ValueError("run policy requires exact executable provenance")
        fixed = (
            (self.requested_model, HARD_BLIND_PILOT_REQUESTED_MODEL_V1, "model"),
            (self.requested_provider, HARD_BLIND_PILOT_REQUESTED_PROVIDER_V1, "provider"),
            (self.requested_effort, HARD_BLIND_PILOT_REQUESTED_EFFORT_V1, "effort"),
            (self.base_instructions, HARD_BLIND_PILOT_BASE_INSTRUCTIONS_V1, "base instructions"),
            (
                self.developer_instructions,
                HARD_BLIND_PILOT_DEVELOPER_INSTRUCTIONS_V1,
                "developer instructions",
            ),
        )
        for actual, expected, label in fixed:
            if type(actual) is not str or actual != expected:
                raise ValueError(f"hard-blind {label} differs from the fixed policy")
        if type(self.timeout_seconds) is not int or (
            self.timeout_seconds != HARD_BLIND_PILOT_TIMEOUT_SECONDS_V1
        ):
            raise ValueError("hard-blind timeout must remain exactly 180 seconds")
        # The ledger enforces ordinal STARTED order.  Selected execution therefore
        # stays single-worker; concurrent calls can race before ordinal zero burns.
        if type(self.max_workers) is not int or self.max_workers != 1:
            raise ValueError("hard-blind selected execution is fixed to one worker")

    @property
    def command_policy_digest(self) -> str:
        return app_server_command_tail_policy_digest(
            self.requested_model,
            self.requested_effort,
        )

    @property
    def process_policy_digest(self) -> str:
        return solver_process_isolation_policy_digest()

    @property
    def request_policy_digest(self) -> str:
        return _digest(_expected_request_policy_material_v1(self))

    def public_payload(self) -> dict[str, Any]:
        self.__post_init__()
        return {
            "base_instruction_digest": _sha256(self.base_instructions.encode("utf-8")),
            "command_policy_digest": self.command_policy_digest,
            "developer_instruction_digest": _sha256(self.developer_instructions.encode("utf-8")),
            "executable": {
                "sha256_digest": self.executable_provenance.sha256_digest,
                "size_bytes": self.executable_provenance.size_bytes,
            },
            "max_workers": self.max_workers,
            "process_isolation_policy_digest": self.process_policy_digest,
            "requested_effort": self.requested_effort,
            "requested_model": self.requested_model,
            "requested_provider": self.requested_provider,
            "request_policy_digest": self.request_policy_digest,
            "timeout_seconds": self.timeout_seconds,
        }


def capture_hard_blind_pilot_run_policy_v1(
    *, source_revision: str, executable_path: str
) -> HardBlindPilotRunPolicyV1:
    """Stat and hash the exact native executable immediately before preregistration."""

    _require_revision(source_revision)
    _require_exact_text(executable_path, "executable path", maximum_bytes=4_096)
    provenance = capture_codex_executable_provenance(executable_path)
    return HardBlindPilotRunPolicyV1(source_revision, provenance)


@dataclass(frozen=True, slots=True)
class HardBlindPilotPrivateMaterialV1:
    """Private caller-held values; none are permitted in a public payload."""

    selection_seed: str
    case_id_key: bytes
    ledger_key: bytes

    def __post_init__(self) -> None:
        seed = _require_exact_text(self.selection_seed, "selection seed", maximum_bytes=256)
        if not seed.isascii():
            raise ValueError("selection seed must be ASCII")
        for value, label in (
            (self.case_id_key, "case-id key"),
            (self.ledger_key, "ledger key"),
        ):
            if type(value) is not bytes or len(value) != 32:
                raise ValueError(f"{label} must be exactly 32 immutable bytes")
        if hmac.compare_digest(self.case_id_key, self.ledger_key):
            raise ValueError("case-id and ledger keys must be independently generated")

    @classmethod
    def generate(cls) -> HardBlindPilotPrivateMaterialV1:
        return cls(
            selection_seed=os.urandom(32).hex(),
            case_id_key=os.urandom(32),
            ledger_key=os.urandom(32),
        )


@dataclass(frozen=True, slots=True)
class PreparedHardBlindPilotSlotV1:
    ordinal: int
    original_case_id: str
    opaque_case_id: str
    case_digest: str
    domain: str
    derivation_family: str
    template: int
    fault_variant: int
    generator_cell: int
    fault_line: int
    slot_id: str
    dispatch: DispatchMaterial
    commitment: SlotCommitment

    def __post_init__(self) -> None:
        if type(self.ordinal) is not int or not 0 <= self.ordinal < HARD_BLIND_PILOT_CASE_COUNT_V1:
            raise ValueError("pilot slot ordinal is invalid")
        for value, label, maximum in (
            (self.original_case_id, "original case id", 64),
            (self.opaque_case_id, "opaque case id", 96),
            (self.domain, "domain", 128),
            (self.derivation_family, "derivation family", 128),
            (self.slot_id, "slot id", 64),
        ):
            _require_exact_text(value, label, maximum_bytes=maximum)
        _require_digest(self.case_digest, "case digest")
        for value, label, minimum, maximum in (
            (self.template, "template", 0, 3),
            (self.fault_variant, "fault variant", 0, 1),
            (self.generator_cell, "generator cell", 0, 1),
            (self.fault_line, "fault line", 3, 10),
        ):
            if type(value) is not int or not minimum <= value <= maximum:
                raise ValueError(f"pilot {label} is invalid")
        if (
            type(self.dispatch) is not DispatchMaterial
            or type(self.commitment) is not SlotCommitment
        ):
            raise ValueError("pilot slot requires exact dispatch and commitment records")
        if self.commitment.slot_id != self.slot_id:
            raise ValueError("pilot slot id differs from its HMAC commitment")


def _current_callbacks_are_pinned() -> None:
    if (
        calibration_bridge.build_hard_calibration_preregistration_v3
        is not _PINNED_BUILD_PREREGISTRATION
        or challenge_module.generate_public_calibration_bank_v2 is not _PINNED_GENERATE_BANK
        or challenge_module.certification_report_v2 is not _PINNED_CERTIFICATION
        or HardChallengeCaseV2.verify_solver_output is not _PINNED_VERIFY_SOLVER_OUTPUT
    ):
        raise ValueError("hard-blind generator or verifier callback changed after import")


def _fixed_sample_cases_v1(
    bank: tuple[HardChallengeCaseV2, ...],
) -> tuple[HardChallengeCaseV2, ...]:
    if type(bank) is not tuple or not all(type(case) is HardChallengeCaseV2 for case in bank):
        raise ValueError("hard-blind sample requires an exact generated bank")
    by_id = {case.case_id: case for case in bank}
    if len(by_id) != len(bank):
        raise ValueError("generated hard bank contains duplicate case identifiers")
    if (
        len(HARD_BLIND_PILOT_ORIGINAL_CASE_IDS_V1) != HARD_BLIND_PILOT_CASE_COUNT_V1
        or len(set(HARD_BLIND_PILOT_ORIGINAL_CASE_IDS_V1)) != HARD_BLIND_PILOT_CASE_COUNT_V1
    ):
        raise ValueError("fixed hard-blind sample is not a unique 64-case order")
    try:
        selected = tuple(by_id[case_id] for case_id in HARD_BLIND_PILOT_ORIGINAL_CASE_IDS_V1)
    except KeyError as exc:
        raise ValueError("fixed hard-blind sample differs from the current generator") from exc
    expected_counts = (
        (
            Counter(case.family for case in selected),
            Counter({family: 16 for family in FAMILY_ORDER_V2}),
        ),
        (
            Counter(case.derivation_family for case in selected),
            Counter({family.value: 16 for family in DERIVATION_FAMILY_ORDER_V2}),
        ),
        (Counter(case.template for case in selected), Counter({index: 16 for index in range(4)})),
        (
            Counter(case.fault_variant for case in selected),
            Counter({index: 32 for index in range(len(FAULT_MECHANISM_ORDER_V2))}),
        ),
        (
            Counter(case.parameter_cell for case in selected),
            Counter({index: 32 for index in range(len(PARAMETER_CELL_ORDER_V2))}),
        ),
        (
            Counter(case.faulty_line for case in selected),
            Counter({line: 8 for line in range(3, 11)}),
        ),
    )
    if any(observed != expected for observed, expected in expected_counts):
        raise ValueError("fixed hard-blind sample lost its exact orthogonal balance")
    if not all(_PINNED_CERTIFICATION(case).passed for case in selected):
        raise ValueError("fixed hard-blind sample failed current mathematical certification")
    return selected


def hard_blind_pilot_balance_v1() -> dict[str, Any]:
    """Validate the current generator and return aggregate answer-free balance counts."""

    _current_callbacks_are_pinned()
    selected = _fixed_sample_cases_v1(_PINNED_GENERATE_BANK())
    return {
        "domains": {family.value: 16 for family in FAMILY_ORDER_V2},
        "fault_lines": {str(line): 8 for line in range(3, 11)},
        "fault_variants": {item.value: 32 for item in FAULT_MECHANISM_ORDER_V2},
        "families": {item.value: 16 for item in DERIVATION_FAMILY_ORDER_V2},
        "generator_cells": {item.value: 32 for item in PARAMETER_CELL_ORDER_V2},
        "templates": {str(index): 16 for index in range(4)},
        "validated_case_count": len(selected),
    }


def _batch_digest_v1(
    *,
    ordinal: int,
    case: HardChallengeCaseV2,
    opaque_case_id: str,
    semantic_program_digest: str,
) -> str:
    return _digest(
        {
            "case_digest": case.digest(),
            "kind": "hard-blind-pilot-batch-v1",
            "opaque_case_id": opaque_case_id,
            "ordinal": ordinal,
            "sample_commitment": HARD_BLIND_PILOT_SAMPLE_COMMITMENT_V1,
            "semantic_program_digest": semantic_program_digest,
        }
    )


def _run_spec_digest_v1(
    *,
    preregistration: HardCalibrationPreregistrationV3,
    policy: HardBlindPilotRunPolicyV1,
) -> str:
    return _digest(
        {
            "calibration_plan_digest": preregistration.plan.digest(),
            "calibration_preregistration_commitment": preregistration.commitment,
            "calibration_public_commitment_digest": (preregistration.public_commitment_digest()),
            "case_count": HARD_BLIND_PILOT_CASE_COUNT_V1,
            "kind": "hard-blind-pilot-run-spec-v1",
            "request_policy_digest": policy.request_policy_digest,
            "response_contract_digest": _sha256(hard_blind_solver_output_schema_json_v1()),
            "sample_commitment": HARD_BLIND_PILOT_SAMPLE_COMMITMENT_V1,
            "source_revision": policy.source_revision,
            "thresholds": {
                "reject_above": HARD_BLIND_PILOT_REJECT_ABOVE_V1,
                "reject_below": HARD_BLIND_PILOT_REJECT_BELOW_V1,
                "target_max": HARD_BLIND_PILOT_TARGET_MAX_V1,
                "target_min": HARD_BLIND_PILOT_TARGET_MIN_V1,
            },
        }
    )


def _prepare_hard_blind_pilot_v1(
    policy: HardBlindPilotRunPolicyV1,
    private_material: HardBlindPilotPrivateMaterialV1,
    *,
    fixed_tokens: tuple[tuple[str, str], ...] | None,
) -> PreparedHardBlindPilotV1:
    if type(policy) is not HardBlindPilotRunPolicyV1:
        raise ValueError("pilot preparation requires an exact run policy")
    if type(private_material) is not HardBlindPilotPrivateMaterialV1:
        raise ValueError("pilot preparation requires exact private material")
    _current_callbacks_are_pinned()
    preregistration = _PINNED_BUILD_PREREGISTRATION(
        private_material.selection_seed,
        case_id_key=private_material.case_id_key,
    )
    bank = _PINNED_GENERATE_BANK()
    selected = _fixed_sample_cases_v1(bank)
    candidate_by_counter = {
        candidate.generator_counter: candidate for candidate in preregistration.corpus.candidates
    }
    case_counter = {case.case_id: index for index, case in enumerate(bank)}
    run_spec_digest = _run_spec_digest_v1(
        preregistration=preregistration,
        policy=policy,
    )
    schema_json = hard_blind_solver_output_schema_json_v1()
    if fixed_tokens is None:
        tokens = tuple((new_slot_id(), _sha256(os.urandom(32))) for _case in selected)
    else:
        if type(fixed_tokens) is not tuple or len(fixed_tokens) != len(selected):
            raise ValueError("private state must retain exactly 64 slot token pairs")
        tokens = fixed_tokens
    if len({slot_id for slot_id, _nonce in tokens}) != len(tokens) or len(
        {nonce for _slot_id, nonce in tokens}
    ) != len(tokens):
        raise ValueError("slot identifiers and dispatch nonces must each be unique")

    slots: list[PreparedHardBlindPilotSlotV1] = []
    for ordinal, (case, token) in enumerate(zip(selected, tokens, strict=True)):
        if type(token) is not tuple or len(token) != 2:
            raise ValueError("slot token must be an exact pair")
        slot_id, nonce = token
        _require_exact_text(slot_id, "slot id", maximum_bytes=64)
        _require_digest(nonce, "dispatch nonce")
        counter = case_counter[case.case_id]
        candidate = candidate_by_counter[counter]
        batch_digest = _batch_digest_v1(
            ordinal=ordinal,
            case=case,
            opaque_case_id=candidate.stable_case_id,
            semantic_program_digest=candidate.semantic_program_digest,
        )
        dispatch = DispatchMaterial(
            run_spec_digest=run_spec_digest,
            batch_digest=batch_digest,
            prompt=hard_blind_solver_prompt_v1(case),
            output_schema_json=schema_json,
            request_policy_digest=policy.request_policy_digest,
            dispatch_nonce=nonce,
        )
        commitment = dispatch.seal(
            key=private_material.ledger_key,
            slot_id=slot_id,
        )
        slots.append(
            PreparedHardBlindPilotSlotV1(
                ordinal=ordinal,
                original_case_id=case.case_id,
                opaque_case_id=candidate.stable_case_id,
                case_digest=case.digest(),
                domain=case.domain,
                derivation_family=case.derivation_family,
                template=case.template,
                fault_variant=case.fault_variant,
                generator_cell=case.parameter_cell,
                fault_line=case.faulty_line,
                slot_id=slot_id,
                dispatch=dispatch,
                commitment=commitment,
            )
        )
    return PreparedHardBlindPilotV1(
        policy=policy,
        private_material=private_material,
        preregistration=preregistration,
        run_spec_digest=run_spec_digest,
        slots=tuple(slots),
    )


@dataclass(frozen=True, slots=True)
class PreparedHardBlindPilotV1:
    """Complete private pilot state, including exact HMAC-bound dispatch material."""

    policy: HardBlindPilotRunPolicyV1
    private_material: HardBlindPilotPrivateMaterialV1
    preregistration: HardCalibrationPreregistrationV3
    run_spec_digest: str
    slots: tuple[PreparedHardBlindPilotSlotV1, ...]

    def __post_init__(self) -> None:
        if type(self.policy) is not HardBlindPilotRunPolicyV1:
            raise ValueError("prepared pilot requires an exact run policy")
        if type(self.private_material) is not HardBlindPilotPrivateMaterialV1:
            raise ValueError("prepared pilot requires exact private material")
        if type(self.preregistration) is not HardCalibrationPreregistrationV3:
            raise ValueError("prepared pilot requires an exact hard preregistration")
        _require_digest(self.run_spec_digest, "run spec digest")
        if type(self.slots) is not tuple or len(self.slots) != HARD_BLIND_PILOT_CASE_COUNT_V1:
            raise ValueError("prepared pilot must retain exactly 64 slots")
        if not all(type(slot) is PreparedHardBlindPilotSlotV1 for slot in self.slots):
            raise ValueError("prepared pilot slots must be exact records")
        if tuple(slot.ordinal for slot in self.slots) != tuple(
            range(HARD_BLIND_PILOT_CASE_COUNT_V1)
        ):
            raise ValueError("prepared pilot slot order is not contiguous")
        if len({slot.slot_id for slot in self.slots}) != len(self.slots):
            raise ValueError("prepared pilot slot identifiers are not unique")
        if tuple(slot.original_case_id for slot in self.slots) != (
            HARD_BLIND_PILOT_ORIGINAL_CASE_IDS_V1
        ):
            raise ValueError("prepared pilot differs from the fixed sample order")

    @property
    def commitments(self) -> tuple[SlotCommitment, ...]:
        return tuple(slot.commitment for slot in self.slots)

    @property
    def attempt_universe_commitment(self) -> str:
        return _digest([slot.commitment.to_dict(ordinal=slot.ordinal) for slot in self.slots])

    def validate_current(self) -> None:
        """Rebuild every answer-free input and HMAC before publication or scoring."""

        self.__post_init__()
        tokens = tuple((slot.slot_id, slot.dispatch.dispatch_nonce) for slot in self.slots)
        rebuilt = _prepare_hard_blind_pilot_v1(
            self.policy,
            self.private_material,
            fixed_tokens=tokens,
        )
        if (
            rebuilt.run_spec_digest != self.run_spec_digest
            or rebuilt.preregistration != self.preregistration
            or rebuilt.slots != self.slots
        ):
            raise ValueError("prepared pilot differs from current source reconstruction")


@dataclass(frozen=True, slots=True)
class BoundHardBlindPilotV1:
    """Prepared pilot fenced to the one ledger digest published before execution.

    This prevents swapping in a separately initialized ledger after publication.
    Copying the pristine bound ledger directory preserves the same digest and is
    outside the cooperative local threat model; an external append-only witness is
    required to prevent a malicious owner from cloning local state.
    """

    prepared: PreparedHardBlindPilotV1
    attempt_ledger_digest: str

    def __post_init__(self) -> None:
        if type(self.prepared) is not PreparedHardBlindPilotV1:
            raise ValueError("ledger binding requires an exact prepared pilot")
        _require_digest(self.attempt_ledger_digest, "bound attempt-ledger digest")

    def validate_ledger(self, ledger: AttemptLedger) -> None:
        if type(ledger) is not AttemptLedger:
            raise ValueError("bound pilot requires an exact attempt ledger")
        if ledger.slots != self.prepared.commitments:
            raise ValueError("bound ledger differs from the prepared slot universe")
        if not hmac.compare_digest(ledger.ledger_digest, self.attempt_ledger_digest):
            raise ValueError("attempt ledger differs from the published ledger digest")

    def public_preregistration_payload(self) -> dict[str, Any]:
        self.prepared.validate_current()
        payload = _public_preregistration_payload_v1(self)
        assert_safe_hard_blind_public_payload_v1(payload, bound=self)
        return payload

    def public_preregistration_digest(self) -> str:
        return _digest(self.public_preregistration_payload())


def _public_preregistration_payload_v1(bound: BoundHardBlindPilotV1) -> dict[str, Any]:
    prepared = bound.prepared
    return {
        "attempt_ledger_digest": bound.attempt_ledger_digest,
        "attempt_policy": {
            "all_committed_slots_in_denominator": True,
            "fresh_process_per_slot": True,
            "nonselected_smoke_required": True,
            "retry_count": 0,
            "usage_limit_after_start": "burned_and_incorrect",
        },
        "attempt_universe_commitment": prepared.attempt_universe_commitment,
        "balance": hard_blind_pilot_balance_v1(),
        "calibration_public_commitment_digest": (
            prepared.preregistration.public_commitment_digest()
        ),
        "case_count": HARD_BLIND_PILOT_CASE_COUNT_V1,
        "model_identity_verified": False,
        "response_contract_digest": _sha256(hard_blind_solver_output_schema_json_v1()),
        "run_policy": prepared.policy.public_payload(),
        "run_spec_digest": prepared.run_spec_digest,
        "sample_commitment": HARD_BLIND_PILOT_SAMPLE_COMMITMENT_V1,
        "schema": HARD_BLIND_PILOT_SCHEMA_V1,
        "schema_version": HARD_BLIND_PILOT_SCHEMA_VERSION_V1,
        "source_revision": prepared.policy.source_revision,
        "thresholds": {
            "reject_above": HARD_BLIND_PILOT_REJECT_ABOVE_V1,
            "reject_below": HARD_BLIND_PILOT_REJECT_BELOW_V1,
            "target_max": HARD_BLIND_PILOT_TARGET_MAX_V1,
            "target_min": HARD_BLIND_PILOT_TARGET_MIN_V1,
        },
    }


def prepare_hard_blind_pilot_v1(
    policy: HardBlindPilotRunPolicyV1,
) -> PreparedHardBlindPilotV1:
    """Generate fresh private keys, nonces, opaque slot ids, and exact commitments."""

    return _prepare_hard_blind_pilot_v1(
        policy,
        HardBlindPilotPrivateMaterialV1.generate(),
        fixed_tokens=None,
    )


def assert_safe_hard_blind_public_payload_v1(
    payload: Any,
    *,
    bound: BoundHardBlindPilotV1,
) -> None:
    """Fail closed unless ``payload`` is the exact aggregate-only public allowlist."""

    if type(bound) is not BoundHardBlindPilotV1:
        raise ValueError("public validation requires an exact bound pilot")
    value = _strict_keys(payload, _PUBLIC_TOP_LEVEL_KEYS_V1, "public preregistration")
    if (
        type(value["schema"]) is not str
        or value["schema"] != HARD_BLIND_PILOT_SCHEMA_V1
        or type(value["schema_version"]) is not int
        or value["schema_version"] != HARD_BLIND_PILOT_SCHEMA_VERSION_V1
    ):
        raise ValueError("public preregistration schema is invalid")
    if type(value["case_count"]) is not int or value["case_count"] != 64:
        raise ValueError("public preregistration case count is invalid")
    if value["model_identity_verified"] is not False:
        raise ValueError("served model identity must remain explicitly unverified")
    for name in (
        "attempt_ledger_digest",
        "attempt_universe_commitment",
        "calibration_public_commitment_digest",
        "response_contract_digest",
        "run_spec_digest",
        "sample_commitment",
    ):
        _require_digest(value[name], name)
    _require_revision(value["source_revision"])
    attempt_policy = _strict_keys(
        value["attempt_policy"],
        {
            "all_committed_slots_in_denominator",
            "fresh_process_per_slot",
            "nonselected_smoke_required",
            "retry_count",
            "usage_limit_after_start",
        },
        "public attempt policy",
    )
    expected_attempt_policy = {
        "all_committed_slots_in_denominator": True,
        "fresh_process_per_slot": True,
        "nonselected_smoke_required": True,
        "retry_count": 0,
        "usage_limit_after_start": "burned_and_incorrect",
    }
    if attempt_policy != expected_attempt_policy or any(
        type(attempt_policy[key]) is not type(expected)
        for key, expected in expected_attempt_policy.items()
    ):
        raise ValueError("public attempt-policy values differ from preregistration")
    thresholds = _strict_keys(
        value["thresholds"],
        {"reject_above", "reject_below", "target_max", "target_min"},
        "public thresholds",
    )
    expected_thresholds = {
        "reject_above": HARD_BLIND_PILOT_REJECT_ABOVE_V1,
        "reject_below": HARD_BLIND_PILOT_REJECT_BELOW_V1,
        "target_max": HARD_BLIND_PILOT_TARGET_MAX_V1,
        "target_min": HARD_BLIND_PILOT_TARGET_MIN_V1,
    }
    if thresholds != expected_thresholds or any(
        type(thresholds[key]) is not float for key in expected_thresholds
    ):
        raise ValueError("public thresholds differ from the strict pilot gates")
    balance = _strict_keys(
        value["balance"],
        {
            "domains",
            "fault_lines",
            "fault_variants",
            "families",
            "generator_cells",
            "templates",
            "validated_case_count",
        },
        "public balance",
    )
    expected_balance = {
        "domains": {family.value: 16 for family in FAMILY_ORDER_V2},
        "fault_lines": {str(line): 8 for line in range(3, 11)},
        "fault_variants": {item.value: 32 for item in FAULT_MECHANISM_ORDER_V2},
        "families": {item.value: 16 for item in DERIVATION_FAMILY_ORDER_V2},
        "generator_cells": {item.value: 32 for item in PARAMETER_CELL_ORDER_V2},
        "templates": {str(index): 16 for index in range(4)},
        "validated_case_count": 64,
    }
    if balance != expected_balance:
        raise ValueError("public balance differs from the fixed 64-case sample")
    run_policy = _strict_keys(
        value["run_policy"],
        {
            "base_instruction_digest",
            "command_policy_digest",
            "developer_instruction_digest",
            "executable",
            "max_workers",
            "process_isolation_policy_digest",
            "requested_effort",
            "requested_model",
            "requested_provider",
            "request_policy_digest",
            "timeout_seconds",
        },
        "public run policy",
    )
    executable = _strict_keys(
        run_policy["executable"],
        {"sha256_digest", "size_bytes"},
        "executable",
    )
    _require_digest(executable["sha256_digest"], "public executable digest")
    if type(executable["size_bytes"]) is not int or executable["size_bytes"] <= 0:
        raise ValueError("public executable size must be a positive exact integer")
    expected_policy_scalars = {
        "max_workers": HARD_BLIND_PILOT_MAX_WORKERS_V1,
        "requested_effort": HARD_BLIND_PILOT_REQUESTED_EFFORT_V1,
        "requested_model": HARD_BLIND_PILOT_REQUESTED_MODEL_V1,
        "requested_provider": HARD_BLIND_PILOT_REQUESTED_PROVIDER_V1,
        "timeout_seconds": HARD_BLIND_PILOT_TIMEOUT_SECONDS_V1,
    }
    if any(
        run_policy[key] != expected or type(run_policy[key]) is not type(expected)
        for key, expected in expected_policy_scalars.items()
    ):
        raise ValueError("public requested runtime differs from the fixed run policy")
    expected_policy_digests = {
        "base_instruction_digest": _sha256(HARD_BLIND_PILOT_BASE_INSTRUCTIONS_V1.encode("utf-8")),
        "command_policy_digest": app_server_command_tail_policy_digest(
            HARD_BLIND_PILOT_REQUESTED_MODEL_V1,
            HARD_BLIND_PILOT_REQUESTED_EFFORT_V1,
        ),
        "developer_instruction_digest": _sha256(
            HARD_BLIND_PILOT_DEVELOPER_INSTRUCTIONS_V1.encode("utf-8")
        ),
        "process_isolation_policy_digest": solver_process_isolation_policy_digest(),
    }
    if any(
        type(run_policy[key]) is not str or not hmac.compare_digest(run_policy[key], expected)
        for key, expected in expected_policy_digests.items()
    ):
        raise ValueError("public run-policy digest differs from the loaded policy")
    expected_request_material = {
        "base_instructions": HARD_BLIND_PILOT_BASE_INSTRUCTIONS_V1,
        "developer_instructions": HARD_BLIND_PILOT_DEVELOPER_INSTRUCTIONS_V1,
        "fixed_thread_policy": {
            "allow_provider_model_fallback": False,
            "approval_policy": "never",
            "dynamic_tools": [],
            "environments": [],
            "ephemeral": True,
            "experimental_raw_events": True,
            "sandbox": "read-only",
        },
        "fixed_turn_policy": {"approval_policy": "never", "environments": []},
        "requested_model": HARD_BLIND_PILOT_REQUESTED_MODEL_V1,
        "requested_provider": HARD_BLIND_PILOT_REQUESTED_PROVIDER_V1,
        "requested_reasoning_effort": HARD_BLIND_PILOT_REQUESTED_EFFORT_V1,
        "timeout_seconds": HARD_BLIND_PILOT_TIMEOUT_SECONDS_V1,
    }
    if type(run_policy["request_policy_digest"]) is not str or not hmac.compare_digest(
        run_policy["request_policy_digest"],
        _digest(expected_request_material),
    ):
        raise ValueError("public request-policy digest differs from the fixed request")
    if not hmac.compare_digest(
        value["response_contract_digest"],
        _sha256(hard_blind_solver_output_schema_json_v1()),
    ):
        raise ValueError("public response-contract digest differs from the fixed schema")
    if not hmac.compare_digest(
        value["sample_commitment"],
        HARD_BLIND_PILOT_SAMPLE_COMMITMENT_V1,
    ):
        raise ValueError("public sample commitment differs from the fixed sample")
    if value != _public_preregistration_payload_v1(bound):
        raise ValueError("public payload differs from the exact bound preregistration")

    def reject_keys(item: Any) -> None:
        if type(item) is dict:
            for key, child in item.items():
                lowered = key.lower()
                if any(fragment in lowered for fragment in _PUBLIC_FORBIDDEN_KEY_FRAGMENTS_V1):
                    raise ValueError("public payload contains a private-field key")
                reject_keys(child)
        elif type(item) is list:
            for child in item:
                reject_keys(child)
        elif type(item) not in {str, int, float, bool, type(None)}:
            raise ValueError("public payload contains a noncanonical value type")

    reject_keys(value)
    encoded = _canonical_json_bytes(value)
    if re.search(
        rb"(?i)(?:DV2-|hard-v3-|numbered derivation|surrounding prose)",
        encoded,
    ):
        raise ValueError("public payload contains suspicious solver-view or response text")
    for original_id in HARD_BLIND_PILOT_ORIGINAL_CASE_IDS_V1:
        if original_id.encode("ascii") in encoded:
            raise ValueError("public payload exposes a fixed sample identifier")
    bank = _PINNED_GENERATE_BANK()
    for case in bank:
        raw_prompt = case.prompt.encode("utf-8")
        if len(raw_prompt) >= 32 and raw_prompt in encoded:
            raise ValueError("public payload exposes a raw solver problem")


def _is_reparse_point_v1(path: Path) -> bool:
    try:
        status = path.lstat()
    except OSError:
        return False
    attributes = int(getattr(status, "st_file_attributes", 0))
    reparse = int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))
    return path.is_symlink() or bool(reparse and attributes & reparse)


def _hard_blind_ledger_root_path_v1(
    ledger_root: str | Path,
    *,
    must_exist: bool,
) -> Path:
    """Validate private ledger placement before creating or reading any ledger bytes."""

    raw = Path(ledger_root)
    if not raw.is_absolute():
        raise ValueError("hard-blind pilot ledger root must be absolute")
    for candidate in (raw.parent, *raw.parent.parents):
        if _is_reparse_point_v1(candidate):
            raise ValueError("hard-blind pilot ledger ancestors may not be reparse points")
    parent = raw.parent.resolve(strict=True)
    if any((candidate / ".git").exists() for candidate in (parent, *parent.parents)):
        raise ValueError("hard-blind pilot ledger must stay outside every Git repository")
    normalized = parent / raw.name
    if must_exist:
        if _is_reparse_point_v1(normalized):
            raise ValueError("hard-blind pilot ledger root may not be a reparse point")
        resolved = normalized.resolve(strict=True)
        if resolved != normalized or not resolved.is_dir():
            raise ValueError("hard-blind pilot ledger root must be a real directory")
        return resolved
    if normalized.exists() or _is_reparse_point_v1(normalized):
        raise FileExistsError(f"hard-blind pilot ledger root already exists: {normalized}")
    return normalized


def create_hard_blind_pilot_ledger_v1(
    prepared: PreparedHardBlindPilotV1,
    ledger_root: str | Path,
) -> AttemptLedger:
    """Create one O_EXCL append-only ledger for the exact prepared slot universe."""

    if type(prepared) is not PreparedHardBlindPilotV1:
        raise ValueError("ledger creation requires an exact prepared pilot")
    prepared.validate_current()
    target = _hard_blind_ledger_root_path_v1(ledger_root, must_exist=False)
    return AttemptLedger.create(
        target,
        key=prepared.private_material.ledger_key,
        slots=prepared.commitments,
    )


def open_hard_blind_pilot_ledger_v1(
    prepared: PreparedHardBlindPilotV1,
    ledger_root: str | Path,
) -> AttemptLedger:
    if type(prepared) is not PreparedHardBlindPilotV1:
        raise ValueError("ledger opening requires an exact prepared pilot")
    prepared.validate_current()
    target = _hard_blind_ledger_root_path_v1(ledger_root, must_exist=True)
    ledger = AttemptLedger.open(target, key=prepared.private_material.ledger_key)
    if ledger.slots != prepared.commitments:
        raise LedgerIntegrityError("pilot ledger differs from the private precommitment")
    return ledger


def bind_hard_blind_pilot_ledger_v1(
    prepared: PreparedHardBlindPilotV1,
    ledger: AttemptLedger,
) -> BoundHardBlindPilotV1:
    """Fence one pristine ledger before publishing or starting any selected slot."""

    if type(prepared) is not PreparedHardBlindPilotV1 or type(ledger) is not AttemptLedger:
        raise ValueError("initial ledger binding requires exact prepared and ledger records")
    prepared.validate_current()
    with _hard_blind_lifecycle_lock_v1(ledger):
        if ledger.slots != prepared.commitments:
            raise ValueError("ledger binding differs from the prepared slot universe")
        if any(ledger.state(slot.slot_id) is not SlotState.PENDING for slot in prepared.slots):
            raise ValueError("ledger must be bound while every selected slot is still pending")
        bound = BoundHardBlindPilotV1(prepared, ledger.ledger_digest)
        bound.validate_ledger(ledger)
        return bound


def restore_bound_hard_blind_pilot_v1(
    prepared: PreparedHardBlindPilotV1,
    ledger: AttemptLedger,
    *,
    published_ledger_digest: str,
) -> BoundHardBlindPilotV1:
    """Restore a published binding after restart without minting a new ledger identity."""

    if type(prepared) is not PreparedHardBlindPilotV1 or type(ledger) is not AttemptLedger:
        raise ValueError("binding restoration requires exact prepared and ledger records")
    prepared.validate_current()
    with _hard_blind_lifecycle_lock_v1(ledger):
        bound = BoundHardBlindPilotV1(prepared, published_ledger_digest)
        bound.validate_ledger(ledger)
        return bound


def _private_state_body_v1(prepared: PreparedHardBlindPilotV1) -> dict[str, Any]:
    return {
        "policy": {
            "base_instructions": prepared.policy.base_instructions,
            "developer_instructions": prepared.policy.developer_instructions,
            "executable_path": prepared.policy.executable_provenance.canonical_path,
            "executable_sha256_digest": prepared.policy.executable_provenance.sha256_digest,
            "executable_size_bytes": prepared.policy.executable_provenance.size_bytes,
            "max_workers": prepared.policy.max_workers,
            "requested_effort": prepared.policy.requested_effort,
            "requested_model": prepared.policy.requested_model,
            "requested_provider": prepared.policy.requested_provider,
            "source_revision": prepared.policy.source_revision,
            "timeout_seconds": prepared.policy.timeout_seconds,
        },
        "private_material": {
            "case_id_key_hex": prepared.private_material.case_id_key.hex(),
            "ledger_key_hex": prepared.private_material.ledger_key.hex(),
            "selection_seed": prepared.private_material.selection_seed,
        },
        "slot_tokens": [
            {
                "dispatch_nonce": slot.dispatch.dispatch_nonce,
                "slot_id": slot.slot_id,
            }
            for slot in prepared.slots
        ],
    }


def serialize_hard_blind_private_state_v1(
    prepared: PreparedHardBlindPilotV1,
) -> bytes:
    if type(prepared) is not PreparedHardBlindPilotV1:
        raise ValueError("private serialization requires an exact prepared pilot")
    prepared.validate_current()
    body = _private_state_body_v1(prepared)
    envelope = {
        "body": body,
        "content_digest": _digest(body),
        "schema": HARD_BLIND_PRIVATE_STATE_SCHEMA_V1,
        "schema_version": HARD_BLIND_PRIVATE_STATE_SCHEMA_VERSION_V1,
    }
    raw = _canonical_json_bytes(envelope)
    if len(raw) > HARD_BLIND_PRIVATE_STATE_MAX_BYTES_V1:
        raise ValueError("private pilot state exceeds its byte budget")
    return raw


def _reject_duplicate_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("private state contains a duplicate JSON key")
        result[key] = value
    return result


def deserialize_hard_blind_private_state_v1(raw: bytes) -> PreparedHardBlindPilotV1:
    if type(raw) is not bytes or not 1 <= len(raw) <= HARD_BLIND_PRIVATE_STATE_MAX_BYTES_V1:
        raise ValueError("private pilot state must be bounded exact bytes")
    try:
        envelope = json.loads(
            raw,
            object_pairs_hook=_reject_duplicate_pairs,
            parse_float=lambda _token: (_ for _ in ()).throw(ValueError("floats forbidden")),
            parse_constant=lambda _token: (_ for _ in ()).throw(ValueError("constants forbidden")),
        )
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError, RecursionError) as exc:
        raise ValueError("private pilot state is invalid JSON") from exc
    if _canonical_json_bytes(envelope) != raw:
        raise ValueError("private pilot state must use canonical JSON bytes")
    value = _strict_keys(
        envelope,
        {"body", "content_digest", "schema", "schema_version"},
        "private state envelope",
    )
    if (
        value["schema"] != HARD_BLIND_PRIVATE_STATE_SCHEMA_V1
        or value["schema_version"] != HARD_BLIND_PRIVATE_STATE_SCHEMA_VERSION_V1
    ):
        raise ValueError("private pilot state schema is unsupported")
    body = _strict_keys(
        value["body"],
        {"policy", "private_material", "slot_tokens"},
        "state body",
    )
    if not hmac.compare_digest(
        _require_digest(value["content_digest"], "state digest"), _digest(body)
    ):
        raise ValueError("private pilot state content digest does not match")
    policy_value = _strict_keys(
        body["policy"],
        {
            "base_instructions",
            "developer_instructions",
            "executable_path",
            "executable_sha256_digest",
            "executable_size_bytes",
            "max_workers",
            "requested_effort",
            "requested_model",
            "requested_provider",
            "source_revision",
            "timeout_seconds",
        },
        "private run policy",
    )
    provenance = ExecutableProvenance(
        canonical_path=policy_value["executable_path"],
        size_bytes=policy_value["executable_size_bytes"],
        sha256_digest=policy_value["executable_sha256_digest"],
    )
    policy = HardBlindPilotRunPolicyV1(
        source_revision=policy_value["source_revision"],
        executable_provenance=provenance,
        requested_model=policy_value["requested_model"],
        requested_provider=policy_value["requested_provider"],
        requested_effort=policy_value["requested_effort"],
        timeout_seconds=policy_value["timeout_seconds"],
        max_workers=policy_value["max_workers"],
        base_instructions=policy_value["base_instructions"],
        developer_instructions=policy_value["developer_instructions"],
    )
    private_value = _strict_keys(
        body["private_material"],
        {"case_id_key_hex", "ledger_key_hex", "selection_seed"},
        "private material",
    )
    try:
        case_id_key = bytes.fromhex(private_value["case_id_key_hex"])
        ledger_key = bytes.fromhex(private_value["ledger_key_hex"])
    except (TypeError, ValueError) as exc:
        raise ValueError("private state keys are not canonical hex") from exc
    if private_value["case_id_key_hex"] != case_id_key.hex() or (
        private_value["ledger_key_hex"] != ledger_key.hex()
    ):
        raise ValueError("private state keys must use lowercase canonical hex")
    private_material = HardBlindPilotPrivateMaterialV1(
        selection_seed=private_value["selection_seed"],
        case_id_key=case_id_key,
        ledger_key=ledger_key,
    )
    token_values = body["slot_tokens"]
    if type(token_values) is not list or len(token_values) != 64:
        raise ValueError("private state slot token universe is incomplete")
    tokens = tuple(
        (
            _strict_keys(token, {"dispatch_nonce", "slot_id"}, "private slot token")["slot_id"],
            token["dispatch_nonce"],
        )
        for token in token_values
    )
    prepared = _prepare_hard_blind_pilot_v1(
        policy,
        private_material,
        fixed_tokens=tokens,
    )
    if _private_state_body_v1(prepared) != body:
        raise ValueError("private state changed during full invariant reconstruction")
    return prepared


def _private_state_path(path: str | Path, *, must_exist: bool) -> Path:
    raw = Path(path)
    if not raw.is_absolute():
        raise ValueError("private state path must be absolute")
    if must_exist:
        try:
            raw_status = raw.lstat()
        except OSError as exc:
            raise ValueError("private state must be an existing regular file") from exc
        if raw.is_symlink() or not stat.S_ISREG(raw_status.st_mode):
            raise ValueError("private state must be a regular non-symlink file")
    parent = raw.parent.resolve(strict=True)
    if any((candidate / ".git").exists() for candidate in (parent, *parent.parents)):
        raise ValueError("private state must stay outside every Git repository")
    normalized = parent / raw.name
    if must_exist:
        resolved = normalized.resolve(strict=True)
        if resolved != normalized:
            raise ValueError("private state must be a regular non-symlink file")
        if any(
            (candidate / ".git").exists()
            for candidate in (resolved.parent, *resolved.parent.parents)
        ):
            raise ValueError("private state must stay outside every Git repository")
        if not resolved.is_file():
            raise ValueError("private state must be a regular non-symlink file")
        normalized = resolved
    return normalized


def save_hard_blind_private_state_v1(
    path: str | Path,
    prepared: PreparedHardBlindPilotV1,
) -> Path:
    """Create a private canonical state file once; never replace an existing path."""

    target = _private_state_path(path, must_exist=False)
    raw = serialize_hard_blind_private_state_v1(prepared)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_BINARY"):
        flags |= os.O_BINARY
    descriptor = os.open(target, flags, 0o600)
    try:
        written = 0
        while written < len(raw):
            count = os.write(descriptor, raw[written:])
            if count <= 0:  # pragma: no cover - defensive short-write guard
                raise OSError("private state write made no progress")
            written += count
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return target


def load_hard_blind_private_state_v1(path: str | Path) -> PreparedHardBlindPilotV1:
    target = _private_state_path(path, must_exist=True)
    try:
        raw = target.read_bytes()
    except OSError as exc:
        raise ValueError("private pilot state could not be read") from exc
    return deserialize_hard_blind_private_state_v1(raw)


def _smoke_case_v1() -> HardChallengeCaseV2:
    selected = set(HARD_BLIND_PILOT_ORIGINAL_CASE_IDS_V1)
    for case in _PINNED_GENERATE_BANK():
        if case.case_id not in selected:
            if not _PINNED_CERTIFICATION(case).passed:
                raise ValueError("nonselected smoke case failed current certification")
            return case
    raise ValueError("current hard bank has no nonselected smoke case")


def _validate_live_runtime_inputs_v1(
    policy: HardBlindPilotRunPolicyV1,
    auth_file: str | Path,
) -> Path:
    """Re-stat launch inputs before smoke and before every selected ``STARTED``."""

    if type(policy) is not HardBlindPilotRunPolicyV1:
        raise ValueError("runtime preflight requires an exact hard-blind policy")
    observed = capture_codex_executable_provenance(policy.executable_provenance.canonical_path)
    if observed != policy.executable_provenance:
        raise ValueError("native executable differs from preregistered SHA and size")
    raw_auth = Path(auth_file)
    if not raw_auth.is_absolute():
        raise ValueError("auth file must be absolute")
    try:
        if raw_auth.is_symlink():
            raise ValueError("auth file must be a regular non-symlink file")
        auth = raw_auth.resolve(strict=True)
        status = auth.lstat()
    except ValueError:
        raise
    except OSError as exc:
        raise ValueError("auth file cannot be inspected") from exc
    if (
        auth.name != "auth.json"
        or auth.is_symlink()
        or not stat.S_ISREG(status.st_mode)
        or not 0 < status.st_size <= MAX_AUTH_FILE_BYTES
    ):
        raise ValueError("auth file fails the runner's regular-file constraints")
    if any((candidate / ".git").exists() for candidate in (auth.parent, *auth.parents)):
        raise ValueError("auth file must stay outside every Git repository")
    return auth


def run_nonselected_hard_blind_smoke_v1(
    policy: HardBlindPilotRunPolicyV1,
    *,
    runtime_directory: str | Path,
    auth_file: str | Path,
) -> CodexRunResult:
    """Run one fresh process on a case outside the 64-case sample and no ledger."""

    if type(policy) is not HardBlindPilotRunPolicyV1:
        raise ValueError("smoke requires an exact hard-blind run policy")
    _current_callbacks_are_pinned()
    case = _smoke_case_v1()
    config = CodexRunConfig(
        cwd=Path(runtime_directory),
        model=policy.requested_model,
        effort=policy.requested_effort,
        model_provider=policy.requested_provider,
        timeout_seconds=policy.timeout_seconds,
        executable=policy.executable_provenance.canonical_path,
        auth_file=Path(auth_file),
        base_instructions=policy.base_instructions,
        developer_instructions=policy.developer_instructions,
        max_transcript_bytes=HARD_BLIND_RUN_MAX_TRANSCRIPT_BYTES_V1,
    )
    return CodexAppServerRunner(config).run(
        prompt=hard_blind_solver_prompt_v1(case),
        output_schema=hard_blind_solver_output_schema_v1(),
    )


def _artifact_matches_policy_v1(
    artifact: CodexRunResult,
    *,
    policy: HardBlindPilotRunPolicyV1,
    expected_prompt: str,
    expected_schema_json: bytes,
) -> bool:
    if type(artifact) is not CodexRunResult:
        return False
    expected = policy.executable_provenance
    return bool(
        artifact.prompt_text == expected_prompt
        and hmac.compare_digest(artifact.output_schema_json, expected_schema_json)
        and artifact.requested_model == policy.requested_model
        and artifact.requested_provider == policy.requested_provider
        and artifact.requested_effort == policy.requested_effort
        and artifact.timeout_seconds == policy.timeout_seconds
        and artifact.executable_path == expected.canonical_path
        and artifact.executable_size_bytes == expected.size_bytes
        and hmac.compare_digest(artifact.executable_sha256_digest, expected.sha256_digest)
        and hmac.compare_digest(artifact.command_tail_policy_digest, policy.command_policy_digest)
        and hmac.compare_digest(
            artifact.solver_process_isolation_policy_digest,
            policy.process_policy_digest,
        )
        and artifact.request_policy_material == _expected_request_policy_material_v1(policy)
        and hmac.compare_digest(
            _digest(artifact.request_policy_material),
            policy.request_policy_digest,
        )
        and artifact.standard_popen_used
        and artifact.codex_home_initial_auth_only
        and artifact.codex_home_cleanup_verified
        and artifact.credential_leak_scan_passed
        and (os.name != "nt" or artifact.executable_lock_enforced)
        and artifact.audit.eligible
        and artifact.audit.accepted_model == policy.requested_model
        and artifact.audit.accepted_provider == policy.requested_provider
        and artifact.audit.accepted_effort == policy.requested_effort
        and artifact.audit.reroutes == ()
        and artifact.audit.final_message_count == 1
        and artifact.audit.model_identity_verified is False
    )


def validate_nonselected_hard_blind_smoke_v1(
    policy: HardBlindPilotRunPolicyV1,
    artifact: CodexRunResult,
) -> str:
    """Return an opaque evidence digest only for an eligible, parseable smoke run."""

    if type(policy) is not HardBlindPilotRunPolicyV1 or type(artifact) is not CodexRunResult:
        raise ValueError("smoke validation requires exact policy and run-result records")
    case = _smoke_case_v1()
    prompt = hard_blind_solver_prompt_v1(case)
    schema = hard_blind_solver_output_schema_json_v1()
    if not _artifact_matches_policy_v1(
        artifact,
        policy=policy,
        expected_prompt=prompt,
        expected_schema_json=schema,
    ):
        raise ValueError("nonselected smoke artifact is ineligible or policy-mismatched")
    if type(artifact.output_text) is not str:
        raise ValueError("nonselected smoke returned no unique text response")
    try:
        SolverOutputV2.from_json(artifact.output_text)
    except (TypeError, ValueError) as exc:
        raise ValueError("nonselected smoke response is not a SolverOutputV2 object") from exc
    return _digest(
        {
            "artifact_execution_id": artifact.protocol_execution_id,
            "kind": "hard-blind-nonselected-smoke-v1",
            "response_digest": _sha256(artifact.output_text.encode("utf-8")),
        }
    )


def _execute_one_slot_v1(
    *,
    prepared: PreparedHardBlindPilotV1,
    ledger: AttemptLedger,
    slot: PreparedHardBlindPilotSlotV1,
    runtime_parent: Path,
    repository_root: Path,
    auth_file: Path,
) -> None:
    def runner(runtime: Path, dispatch: DispatchMaterial) -> ArtifactPayload:
        config = CodexRunConfig(
            cwd=runtime,
            model=prepared.policy.requested_model,
            effort=prepared.policy.requested_effort,
            model_provider=prepared.policy.requested_provider,
            timeout_seconds=prepared.policy.timeout_seconds,
            executable=prepared.policy.executable_provenance.canonical_path,
            auth_file=auth_file,
            base_instructions=prepared.policy.base_instructions,
            developer_instructions=prepared.policy.developer_instructions,
            max_transcript_bytes=HARD_BLIND_RUN_MAX_TRANSCRIPT_BYTES_V1,
        )
        artifact = CodexAppServerRunner(config).run(
            prompt=dispatch.prompt,
            output_schema=json.loads(dispatch.output_schema_json),
        )
        return ArtifactPayload(
            serialize_codex_run_result(artifact),
            HARD_BLIND_PRIVATE_ARTIFACT_MEDIA_TYPE_V1,
        )

    try:
        ledger.execute_first_attempt(
            slot.slot_id,
            runtime_parent=runtime_parent,
            repository_root=repository_root,
            dispatch=slot.dispatch,
            runner=runner,
        )
    except AttemptRunnerFailed:
        # execute_first_attempt already persisted a sanitized FAILED transition.
        return


def _hard_blind_lifecycle_lock_path_v1(ledger: AttemptLedger) -> Path:
    """Return the persistent lock inode beside, never inside, the strict ledger root."""

    root = _hard_blind_ledger_root_path_v1(ledger.root, must_exist=True)
    return root.parent / f".{root.name}.hard-blind-pilot-v1.lock"


@contextmanager
def _hard_blind_lifecycle_lock_v1(ledger: AttemptLedger) -> Iterator[None]:
    """Fail closed when execute, recovery, progress, or scoring is already active.

    Ownership is a kernel byte lock, not file existence.  The one-byte sibling file is
    intentionally persistent, while ownership is released automatically if the process
    dies.  Keeping it outside the ledger directory preserves the ledger's strict layout.
    """

    if type(ledger) is not AttemptLedger:
        raise ValueError("hard-blind lifecycle locking requires an exact ledger")
    path = _hard_blind_lifecycle_lock_path_v1(ledger)
    if path.is_symlink():
        raise ValueError("hard-blind lifecycle lock may not be a symlink")
    flags = os.O_RDWR | os.O_CREAT
    for optional_flag in ("O_BINARY", "O_NOINHERIT", "O_NOFOLLOW"):
        flags |= int(getattr(os, optional_flag, 0))
    descriptor = os.open(path, flags, 0o600)
    locked = False
    try:
        status = os.fstat(descriptor)
        path_status = path.lstat()
        if (
            path.is_symlink()
            or not stat.S_ISREG(status.st_mode)
            or not stat.S_ISREG(path_status.st_mode)
            or (status.st_dev, status.st_ino) != (path_status.st_dev, path_status.st_ino)
            or status.st_nlink != 1
        ):
            raise ValueError("hard-blind lifecycle lock must be a regular file")
        if status.st_size == 0:
            os.lseek(descriptor, 0, os.SEEK_SET)
            if os.write(descriptor, b"\0") != 1:  # pragma: no cover - defensive short write
                raise OSError("hard-blind lifecycle lock initialization made no progress")
            os.fsync(descriptor)
        elif status.st_size != 1:
            raise ValueError("hard-blind lifecycle lock has an invalid size")
        os.lseek(descriptor, 0, os.SEEK_SET)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise RuntimeError(
                "hard-blind lifecycle is already active in another collector"
            ) from exc
        locked = True
        yield
    finally:
        if locked:
            os.lseek(descriptor, 0, os.SEEK_SET)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def execute_hard_blind_pilot_v1(
    bound: BoundHardBlindPilotV1,
    *,
    ledger: AttemptLedger,
    smoke_runtime_directory: str | Path,
    runtime_parent: str | Path,
    repository_root: str | Path,
    auth_file: str | Path,
    max_workers: int = HARD_BLIND_PILOT_MAX_WORKERS_V1,
) -> HardBlindPilotScoringV1:
    """Execute pending slots once, sequentially, then score durable artifacts only."""

    if type(bound) is not BoundHardBlindPilotV1 or type(ledger) is not AttemptLedger:
        raise ValueError("execution requires exact bound-pilot and ledger records")
    if type(max_workers) is not int or max_workers != 1:
        raise ValueError("selected pilot execution is fixed to max_workers=1")
    if HARD_BLIND_PILOT_COLLECTION_ENABLED_V1 is not True:
        raise RuntimeError(
            "hard-blind pilot v1 selected collection is disabled: "
            + HARD_BLIND_PILOT_COLLECTION_BLOCKER_V1
        )
    with _hard_blind_lifecycle_lock_v1(ledger):
        prepared = bound.prepared
        prepared.validate_current()
        bound.validate_ledger(ledger)
        runtime_root = Path(runtime_parent).resolve(strict=True)
        repository = Path(repository_root).resolve(strict=True)
        auth = _validate_live_runtime_inputs_v1(prepared.policy, auth_file)
        # A fresh live smoke is unavoidable on every execution entry.  It occurs before
        # even inspecting/allocating the first selected runtime, so an old successful
        # artifact cannot mask today's quota, schema, auth, or service failure.
        smoke_artifact = run_nonselected_hard_blind_smoke_v1(
            prepared.policy,
            runtime_directory=smoke_runtime_directory,
            auth_file=auth,
        )
        validate_nonselected_hard_blind_smoke_v1(prepared.policy, smoke_artifact)
        ledger.recover_orphans()
        for slot in prepared.slots:
            state = ledger.state(slot.slot_id)
            if state is SlotState.PENDING:
                auth = _validate_live_runtime_inputs_v1(prepared.policy, auth)
                _execute_one_slot_v1(
                    prepared=prepared,
                    ledger=ledger,
                    slot=slot,
                    runtime_parent=runtime_root,
                    repository_root=repository,
                    auth_file=auth,
                )
            # RETURNED/FAILED/STARTED slots are burned and never dispatched again.
        return _score_hard_blind_pilot_locked_v1(bound, ledger=ledger)


class HardBlindPilotAttemptStatusV1(StrEnum):
    CORRECT = "correct"
    INCORRECT = "incorrect"
    UNPARSEABLE = "unparseable"
    INELIGIBLE = "ineligible"
    ARTIFACT_INVALID = "artifact_invalid"
    RUNNER_FAILED = "runner_failed"
    NOT_RETURNED = "not_returned"


@dataclass(frozen=True, slots=True)
class HardBlindPilotCaseResultV1:
    ordinal: int
    original_case_id: str
    domain: str
    derivation_family: str
    status: HardBlindPilotAttemptStatusV1
    correct: bool
    response_digest: str
    verifier_evidence_digest: str

    def __post_init__(self) -> None:
        if type(self.ordinal) is not int or not 0 <= self.ordinal < 64:
            raise ValueError("case-result ordinal is invalid")
        _require_exact_text(self.original_case_id, "case-result id", maximum_bytes=64)
        _require_exact_text(self.domain, "case-result domain", maximum_bytes=128)
        _require_exact_text(self.derivation_family, "case-result family", maximum_bytes=128)
        if type(self.status) is not HardBlindPilotAttemptStatusV1 or type(self.correct) is not bool:
            raise ValueError("case result has invalid status or correctness")
        if self.correct != (self.status is HardBlindPilotAttemptStatusV1.CORRECT):
            raise ValueError("only a correct status may carry a true score")
        _require_digest(self.response_digest, "response digest")
        _require_digest(self.verifier_evidence_digest, "verifier evidence digest")


@dataclass(frozen=True, slots=True)
class HardBlindPilotScoringV1:
    run_spec_digest: str
    attempt_ledger_digest: str
    results: tuple[HardBlindPilotCaseResultV1, ...]

    def __post_init__(self) -> None:
        _require_digest(self.run_spec_digest, "scoring run-spec digest")
        _require_digest(self.attempt_ledger_digest, "scoring ledger digest")
        if (
            type(self.results) is not tuple
            or len(self.results) != 64
            or not all(type(result) is HardBlindPilotCaseResultV1 for result in self.results)
        ):
            raise ValueError("scoring must retain exactly 64 exact case results")
        if tuple(result.ordinal for result in self.results) != tuple(range(64)):
            raise ValueError("scoring result order differs from the committed sample")
        if tuple(result.original_case_id for result in self.results) != (
            HARD_BLIND_PILOT_ORIGINAL_CASE_IDS_V1
        ):
            raise ValueError("scoring case identities differ from the committed sample")

    @property
    def correct_count(self) -> int:
        return sum(result.correct for result in self.results)

    @property
    def accuracy(self) -> float:
        return self.correct_count / HARD_BLIND_PILOT_CASE_COUNT_V1

    @property
    def decision(self) -> str:
        value = self.accuracy
        if value < HARD_BLIND_PILOT_REJECT_BELOW_V1:
            return "reject_too_hard"
        if value > HARD_BLIND_PILOT_REJECT_ABOVE_V1:
            return "reject_too_easy"
        if HARD_BLIND_PILOT_TARGET_MIN_V1 <= value <= HARD_BLIND_PILOT_TARGET_MAX_V1:
            return "target_band"
        return "outside_target_not_rejected"

    def privacy_safe_summary(self) -> dict[str, Any]:
        def group(field: str) -> dict[str, Any]:
            labels = sorted({getattr(result, field) for result in self.results})
            output: dict[str, Any] = {}
            for label in labels:
                subset = tuple(result for result in self.results if getattr(result, field) == label)
                correct = sum(result.correct for result in subset)
                output[label] = {
                    "accuracy": correct / len(subset),
                    "correct": correct,
                    "total": len(subset),
                }
            return output

        statuses = Counter(result.status.value for result in self.results)
        return {
            "accuracy": self.accuracy,
            "attempt_ledger_digest": self.attempt_ledger_digest,
            "case_count": HARD_BLIND_PILOT_CASE_COUNT_V1,
            "correct": self.correct_count,
            "decision": self.decision,
            "model_identity_verified": False,
            "per_domain": group("domain"),
            "per_family": group("derivation_family"),
            "run_spec_digest": self.run_spec_digest,
            "status_counts": {key: statuses[key] for key in sorted(statuses)},
        }


def _nonresponse_result_v1(
    slot: PreparedHardBlindPilotSlotV1,
    *,
    status: HardBlindPilotAttemptStatusV1,
    response_material: Mapping[str, Any],
) -> HardBlindPilotCaseResultV1:
    response_digest = _digest(response_material)
    evidence = _digest(
        {
            "kind": "hard-blind-pilot-nonresponse-v1",
            "response_digest": response_digest,
            "status": status.value,
        }
    )
    return HardBlindPilotCaseResultV1(
        ordinal=slot.ordinal,
        original_case_id=slot.original_case_id,
        domain=slot.domain,
        derivation_family=slot.derivation_family,
        status=status,
        correct=False,
        response_digest=response_digest,
        verifier_evidence_digest=evidence,
    )


def hard_blind_pilot_progress_v1(
    bound: BoundHardBlindPilotV1,
    *,
    ledger: AttemptLedger,
) -> dict[str, Any]:
    """Recover durable orphans and return state-only progress under a collector lock."""

    if type(bound) is not BoundHardBlindPilotV1 or type(ledger) is not AttemptLedger:
        raise ValueError("progress requires exact bound-pilot and ledger records")
    with _hard_blind_lifecycle_lock_v1(ledger):
        return _hard_blind_pilot_progress_locked_v1(bound, ledger=ledger)


def _hard_blind_pilot_progress_locked_v1(
    bound: BoundHardBlindPilotV1,
    *,
    ledger: AttemptLedger,
) -> dict[str, Any]:
    """Finalize recoverable outcomes; residual STARTED slots are burned and scorable."""

    bound.validate_ledger(ledger)
    ledger.recover_orphans()
    states = tuple(ledger.state(slot.slot_id) for slot in bound.prepared.slots)
    counts = Counter(state.value for state in states)
    terminal = sum(state in {SlotState.RETURNED, SlotState.FAILED} for state in states)
    burned = sum(state is SlotState.STARTED for state in states)
    pending = sum(state is SlotState.PENDING for state in states)
    return {
        "attempt_ledger_digest": bound.attempt_ledger_digest,
        "burned_without_outcome_count": burned,
        "case_count": HARD_BLIND_PILOT_CASE_COUNT_V1,
        "complete": pending == 0,
        "scorable_count": terminal + burned,
        "state_counts": {key: counts[key] for key in sorted(counts)},
        "terminal_count": terminal,
    }


def score_hard_blind_pilot_v1(
    bound: BoundHardBlindPilotV1,
    *,
    ledger: AttemptLedger,
) -> HardBlindPilotScoringV1:
    """Recover then score all 64 slots, rejecting any still-unstarted slot."""

    if type(bound) is not BoundHardBlindPilotV1 or type(ledger) is not AttemptLedger:
        raise ValueError("scoring requires exact bound-pilot and ledger records")
    with _hard_blind_lifecycle_lock_v1(ledger):
        return _score_hard_blind_pilot_locked_v1(bound, ledger=ledger)


def _score_hard_blind_pilot_locked_v1(
    bound: BoundHardBlindPilotV1,
    *,
    ledger: AttemptLedger,
) -> HardBlindPilotScoringV1:
    """Score at the exclusive post-run boundary after orphan recovery."""

    prepared = bound.prepared
    prepared.validate_current()
    bound.validate_ledger(ledger)
    progress = _hard_blind_pilot_progress_locked_v1(bound, ledger=ledger)
    if progress["complete"] is not True:
        raise RuntimeError("empirical scoring requires all 64 selected slots to leave PENDING")
    bank_by_id = {case.case_id: case for case in _PINNED_GENERATE_BANK()}
    results: list[HardBlindPilotCaseResultV1] = []
    schema_json = hard_blind_solver_output_schema_json_v1()
    for slot in prepared.slots:
        case = bank_by_id[slot.original_case_id]
        if not hmac.compare_digest(case.digest(), slot.case_digest) or not (
            _PINNED_CERTIFICATION(case).passed
        ):
            raise ValueError("scoring case differs from its certified private precommitment")
        state = ledger.state(slot.slot_id)
        if state is SlotState.FAILED:
            selected = ledger.selected_outcome(slot.slot_id)
            results.append(
                _nonresponse_result_v1(
                    slot,
                    status=HardBlindPilotAttemptStatusV1.RUNNER_FAILED,
                    response_material={
                        "kind": "runner-failed",
                        "object_digest": selected.object_digest if selected else None,
                    },
                )
            )
            continue
        if state is not SlotState.RETURNED:
            results.append(
                _nonresponse_result_v1(
                    slot,
                    status=HardBlindPilotAttemptStatusV1.NOT_RETURNED,
                    response_material={"kind": "not-returned", "ledger_state": state.value},
                )
            )
            continue
        stored = ledger.selected_outcome(slot.slot_id)
        if stored is None or stored.kind is not OutcomeKind.RETURNED:
            raise LedgerIntegrityError("returned slot lacks its selected outcome")
        payload = ledger.load_selected_artifact(slot.slot_id)
        if payload.media_type != HARD_BLIND_PRIVATE_ARTIFACT_MEDIA_TYPE_V1:
            results.append(
                _nonresponse_result_v1(
                    slot,
                    status=HardBlindPilotAttemptStatusV1.ARTIFACT_INVALID,
                    response_material={
                        "kind": "wrong-media",
                        "object_digest": stored.object_digest,
                    },
                )
            )
            continue
        try:
            artifact = deserialize_codex_run_result(payload.content)
        except (CodexArtifactError, TypeError, ValueError):
            results.append(
                _nonresponse_result_v1(
                    slot,
                    status=HardBlindPilotAttemptStatusV1.ARTIFACT_INVALID,
                    response_material={
                        "kind": "invalid-artifact",
                        "object_digest": stored.object_digest,
                    },
                )
            )
            continue
        if not _artifact_matches_policy_v1(
            artifact,
            policy=prepared.policy,
            expected_prompt=slot.dispatch.prompt,
            expected_schema_json=schema_json,
        ):
            results.append(
                _nonresponse_result_v1(
                    slot,
                    status=HardBlindPilotAttemptStatusV1.INELIGIBLE,
                    response_material={
                        "kind": "ineligible-artifact",
                        "object_digest": stored.object_digest,
                    },
                )
            )
            continue
        output = artifact.output_text
        response_digest = (
            _sha256(output.encode("utf-8"))
            if type(output) is str
            else _digest({"kind": "missing-output", "object_digest": stored.object_digest})
        )
        try:
            parsed = SolverOutputV2.from_json(output) if type(output) is str else None
        except (TypeError, ValueError):
            parsed = None
        if parsed is None:
            _passed, evidence = _PINNED_VERIFY_SOLVER_OUTPUT(case, output or "")
            results.append(
                HardBlindPilotCaseResultV1(
                    ordinal=slot.ordinal,
                    original_case_id=slot.original_case_id,
                    domain=slot.domain,
                    derivation_family=slot.derivation_family,
                    status=HardBlindPilotAttemptStatusV1.UNPARSEABLE,
                    correct=False,
                    response_digest=response_digest,
                    verifier_evidence_digest=evidence,
                )
            )
            continue
        passed, evidence = _PINNED_VERIFY_SOLVER_OUTPUT(case, parsed)
        status = (
            HardBlindPilotAttemptStatusV1.CORRECT
            if passed
            else HardBlindPilotAttemptStatusV1.INCORRECT
        )
        results.append(
            HardBlindPilotCaseResultV1(
                ordinal=slot.ordinal,
                original_case_id=slot.original_case_id,
                domain=slot.domain,
                derivation_family=slot.derivation_family,
                status=status,
                correct=passed,
                response_digest=response_digest,
                verifier_evidence_digest=evidence,
            )
        )
    return HardBlindPilotScoringV1(
        run_spec_digest=prepared.run_spec_digest,
        attempt_ledger_digest=ledger.ledger_digest,
        results=tuple(results),
    )


__all__ = [
    "HARD_BLIND_PILOT_BASE_INSTRUCTIONS_V1",
    "HARD_BLIND_PILOT_CASE_COUNT_V1",
    "HARD_BLIND_PILOT_COLLECTION_BLOCKER_V1",
    "HARD_BLIND_PILOT_COLLECTION_ENABLED_V1",
    "HARD_BLIND_PILOT_DEVELOPER_INSTRUCTIONS_V1",
    "HARD_BLIND_PILOT_HISTORICAL_SAMPLE_DIGEST_V1",
    "HARD_BLIND_PILOT_MAX_WORKERS_V1",
    "HARD_BLIND_PILOT_ORIGINAL_CASE_IDS_V1",
    "HARD_BLIND_PILOT_SAMPLE_COMMITMENT_V1",
    "HARD_BLIND_PILOT_SCHEMA_V1",
    "HARD_BLIND_PILOT_SCHEMA_VERSION_V1",
    "HARD_BLIND_PRIVATE_ARTIFACT_MEDIA_TYPE_V1",
    "BoundHardBlindPilotV1",
    "HardBlindPilotAttemptStatusV1",
    "HardBlindPilotCaseResultV1",
    "HardBlindPilotPrivateMaterialV1",
    "HardBlindPilotRunPolicyV1",
    "HardBlindPilotScoringV1",
    "PreparedHardBlindPilotSlotV1",
    "PreparedHardBlindPilotV1",
    "assert_safe_hard_blind_public_payload_v1",
    "bind_hard_blind_pilot_ledger_v1",
    "capture_hard_blind_pilot_run_policy_v1",
    "create_hard_blind_pilot_ledger_v1",
    "deserialize_hard_blind_private_state_v1",
    "execute_hard_blind_pilot_v1",
    "hard_blind_pilot_balance_v1",
    "hard_blind_pilot_progress_v1",
    "hard_blind_solver_output_schema_json_v1",
    "hard_blind_solver_output_schema_v1",
    "hard_blind_solver_prompt_v1",
    "load_hard_blind_private_state_v1",
    "open_hard_blind_pilot_ledger_v1",
    "prepare_hard_blind_pilot_v1",
    "restore_bound_hard_blind_pilot_v1",
    "run_nonselected_hard_blind_smoke_v1",
    "save_hard_blind_private_state_v1",
    "score_hard_blind_pilot_v1",
    "serialize_hard_blind_private_state_v1",
    "validate_nonselected_hard_blind_smoke_v1",
]
