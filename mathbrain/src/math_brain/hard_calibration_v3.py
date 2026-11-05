"""Outcome-blind v3 preregistration for the public hard-challenge-v2 bank.

This is the integration layer between the mathematical generator and the generic
calibration protocol.  It fixes the complete 320-case rendering, uses opaque stable
solver identifiers, commits one plan-wide verifier identity map, and predeclares the
two implementations that must execute for each mathematical domain.
"""

from __future__ import annotations

import hashlib
import hmac
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import calibration_plan_v3 as plan_module
from . import calibration_protocol_v3 as protocol_module
from . import calibration_v2 as calibration_module
from . import models as models_module
from .calibration_plan_v3 import AxisCounts, CalibrationPlan
from .calibration_protocol_v3 import (
    CalibrationOutcomeBinding,
    PreregisteredCorpus,
    PreregistrationCandidate,
    VerifierIdentityBinding,
    build_preregistered_corpus,
)
from .calibration_v2 import CalibrationCase, OracleReceipt
from .hard_challenge_v2 import (
    CALIBRATION_CASE_COUNT_V2,
    CALIBRATION_ELIGIBLE_FAULT_LINES_V2,
    CALIBRATION_SCHEDULE_VERSION_V2,
    DERIVATION_FAMILY_ORDER_V2,
    FAMILY_ORDER_V2,
    GENERATOR_VERSION_V2,
    PARAMETER_CELL_ORDER_V2,
    PINNED_VERIFIER_ARTIFACT_DIGEST_V2,
    PINNED_VERIFIER_SOURCE_DIGEST_V2,
    HardChallengeCaseV2,
    calibration_schedule_digest_v2,
    certification_report_v2,
    content_digest_v2,
    generate_public_calibration_bank_v2,
)

HARD_CALIBRATION_V3_SCHEMA = "org.math-research-brain/hard-calibration-preregistration"
HARD_CALIBRATION_V3_SCHEMA_VERSION = 1
HARD_CALIBRATION_PUBLIC_COMMITMENT_SCHEMA = (
    "org.math-research-brain/hard-calibration-public-commitment"
)
HARD_CALIBRATION_PUBLIC_COMMITMENT_SCHEMA_VERSION = 1
HARD_CALIBRATION_LAYOUT_LABEL = "public-hard-v2/relational-twelve-line"
HARD_CALIBRATION_CASE_ID_KEY_BYTES = 32
_CASE_ID_DOMAIN = b"math-brain/hard-calibration-v3/case-id/v1\x00"
_CASE_ID_KEY_COMMITMENT_DOMAIN = b"math-brain/hard-calibration-v3/key-commitment/v1\x00"
_PINNED_OUTCOME_BINDER = protocol_module.bind_calibration_outcomes
_PINNED_OUTCOME_VERIFIER = protocol_module.verify_calibration_outcome_binding


def _normalized_source_digest(path: Path) -> str:
    """Hash UTF-8 source with platform newline differences normalized away."""

    try:
        text = path.read_bytes().decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ValueError("calibration protocol source is unavailable as UTF-8") from exc
    normalized = text.replace("\r\n", "\n").replace("\r", "\n").encode()
    return "sha256:" + hashlib.sha256(normalized).hexdigest()


def _evaluator_protocol_digest() -> str:
    sources = {
        "calibration_case": getattr(calibration_module, "__file__", None),
        "calibration_plan": getattr(plan_module, "__file__", None),
        "calibration_protocol": getattr(protocol_module, "__file__", None),
        "hard_calibration_bridge": __file__,
        "trusted_models": getattr(models_module, "__file__", None),
    }
    if any(type(source) is not str or not source for source in sources.values()):
        raise ValueError("calibration evaluator source path is unavailable")
    return content_digest_v2(
        {
            "kind": "hard-calibration-evaluator-source-bundle-v3",
            "sources": {
                name: _normalized_source_digest(Path(source))
                for name, source in sorted(sources.items())
                if isinstance(source, str)
            },
        }
    )


def _generator_bundle_digest() -> str:
    return content_digest_v2(
        {
            "generator_source_digest": PINNED_VERIFIER_SOURCE_DIGEST_V2,
            "generator_version": GENERATOR_VERSION_V2,
            "kind": "hard-calibration-generator-bundle-v3",
            "schedule_digest": calibration_schedule_digest_v2(),
            "schedule_version": CALIBRATION_SCHEDULE_VERSION_V2,
            "verifier_artifact_digest": PINNED_VERIFIER_ARTIFACT_DIGEST_V2,
        }
    )


def _derivation_algorithm_digest() -> str:
    return content_digest_v2(
        {
            "generator_source_digest": PINNED_VERIFIER_SOURCE_DIGEST_V2,
            "generator_version": GENERATOR_VERSION_V2,
            "kind": "hard-calibration-derivation-algorithm-v3",
            "schedule_digest": calibration_schedule_digest_v2(),
            "schedule_version": CALIBRATION_SCHEDULE_VERSION_V2,
        }
    )


def _verifier_identities(
    cases: tuple[HardChallengeCaseV2, ...],
) -> tuple[VerifierIdentityBinding, ...]:
    names: dict[str, str] = {}
    for case in cases:
        for name, digest in (
            (case.primary_verifier, case.primary_oracle_digest),
            (case.independent_verifier, case.independent_oracle_digest),
        ):
            previous = names.setdefault(digest, name)
            if previous != name:
                raise ValueError("one verifier digest is paired with multiple identities")
    return tuple(
        VerifierIdentityBinding(
            implementation_digest=digest,
            verifier_id=f"{names[digest]}@{digest}",
        )
        for digest in sorted(names)
    )


def _template_fingerprints() -> tuple[str, ...]:
    return tuple(
        content_digest_v2(
            {
                "generator_source_digest": PINNED_VERIFIER_SOURCE_DIGEST_V2,
                "generator_version": GENERATOR_VERSION_V2,
                "kind": "position-blind-discourse-template-v3",
                "template_index": index,
            }
        )
        for index in range(4)
    )


def _generator_cell_signatures() -> tuple[str, ...]:
    return tuple(
        content_digest_v2(
            {
                "generator_source_digest": PINNED_VERIFIER_SOURCE_DIGEST_V2,
                "generator_version": GENERATOR_VERSION_V2,
                "kind": "structural-parameter-cell-v3",
                "parameter_cell": cell.value,
            }
        )
        for cell in PARAMETER_CELL_ORDER_V2
    )


def _derivation_parameters_digest(*, case_count: int, case_id_key_commitment: str) -> str:
    return content_digest_v2(
        {
            "case_count": case_count,
            "case_id_key_commitment": case_id_key_commitment,
            "derivation_families": [item.value for item in DERIVATION_FAMILY_ORDER_V2],
            "eligible_fault_lines": list(CALIBRATION_ELIGIBLE_FAULT_LINES_V2),
            "kind": "hard-calibration-derivation-parameters-v3",
            "parameter_cells": [item.value for item in PARAMETER_CELL_ORDER_V2],
            "schedule_digest": calibration_schedule_digest_v2(),
            "schedule_version": CALIBRATION_SCHEDULE_VERSION_V2,
        }
    )


def _plan_for(
    cases: tuple[HardChallengeCaseV2, ...],
    identities: tuple[VerifierIdentityBinding, ...],
    case_id_key_commitment: str,
) -> CalibrationPlan:
    return CalibrationPlan.build(
        generator_source_digest=PINNED_VERIFIER_SOURCE_DIGEST_V2,
        generator_bundle_digest=_generator_bundle_digest(),
        evaluator_protocol_digest=_evaluator_protocol_digest(),
        derivation_algorithm_digest=_derivation_algorithm_digest(),
        derivation_parameters_digest=_derivation_parameters_digest(
            case_count=len(cases),
            case_id_key_commitment=case_id_key_commitment,
        ),
        case_count=len(cases),
        axis_counts=AxisCounts(
            family=len(DERIVATION_FAMILY_ORDER_V2),
            template=4,
            layout=1,
            domain=len(FAMILY_ORDER_V2),
            fault=2,
            generator_cell=len(PARAMETER_CELL_ORDER_V2),
        ),
        eligible_arities=(len(CALIBRATION_ELIGIBLE_FAULT_LINES_V2),),
        position_blind_template_fingerprints=_template_fingerprints(),
        generator_cell_signatures=_generator_cell_signatures(),
        verifier_implementation_digests=tuple(
            identity.implementation_digest for identity in identities
        ),
    )


def _case_id_key_commitment(case_id_key: bytes) -> str:
    if type(case_id_key) is not bytes or len(case_id_key) != HARD_CALIBRATION_CASE_ID_KEY_BYTES:
        raise ValueError("hard calibration case-id key must be exactly 32 immutable bytes")
    return "sha256:" + hashlib.sha256(_CASE_ID_KEY_COMMITMENT_DOMAIN + case_id_key).hexdigest()


def _opaque_stable_case_id(case: HardChallengeCaseV2, case_id_key: bytes) -> str:
    _case_id_key_commitment(case_id_key)
    digest = hmac.new(
        case_id_key,
        _CASE_ID_DOMAIN + case.digest().encode("ascii"),
        hashlib.sha256,
    ).hexdigest()
    return "hard-v3-" + digest


def _candidate(
    case: HardChallengeCaseV2,
    *,
    counter: int,
    plan: CalibrationPlan,
    identities: tuple[VerifierIdentityBinding, ...],
    case_id_key: bytes,
) -> PreregistrationCandidate:
    report = certification_report_v2(case)
    if not report.passed:
        raise ValueError("hard case failed exact mathematical certification")
    template_fingerprints = plan.position_blind_template_fingerprints
    cell_signatures = plan.generator_cell_signatures
    return PreregistrationCandidate(
        stable_case_id=_opaque_stable_case_id(case, case_id_key),
        generator_counter=counter,
        family_index=case.derivation_family_index,
        family_label=case.calibration_family,
        template_index=case.template,
        template_label=case.template_id,
        layout_index=0,
        layout_label=HARD_CALIBRATION_LAYOUT_LABEL,
        domain_index=FAMILY_ORDER_V2.index(case.family),
        domain_label=case.domain,
        fault_index=case.fault_variant,
        fault_label=case.mechanism,
        generator_cell_index=case.parameter_cell,
        generator_cell_label=case.generator_cell,
        template_fingerprint=template_fingerprints[case.template],
        generator_cell_signature=cell_signatures[case.parameter_cell],
        verifier_implementation_digests=plan.verifier_implementation_digests,
        verifier_identities=identities,
        executed_verifier_implementation_digests=tuple(
            sorted((case.primary_oracle_digest, case.independent_oracle_digest))
        ),
        semantic_program_digest=content_digest_v2(case.semantic_program.to_dict()),
        prompt=case.prompt,
        faulty_trace=case.trace(faulty=True),
        eligible_fault_lines=CALIBRATION_ELIGIBLE_FAULT_LINES_V2,
        fault_line=case.faulty_line,
    )


@dataclass(frozen=True, slots=True)
class HardCalibrationPreregistrationV3:
    """Complete evaluator-side hard-v2 preregistration before model outcomes."""

    plan: CalibrationPlan
    corpus: PreregisteredCorpus
    verifier_identities: tuple[VerifierIdentityBinding, ...]
    case_id_key_commitment: str
    case_id_key: bytes = field(repr=False, compare=False)

    def __post_init__(self) -> None:
        if type(self.plan) is not CalibrationPlan or type(self.corpus) is not PreregisteredCorpus:
            raise ValueError("hard calibration preregistration requires exact protocol records")
        if type(self.verifier_identities) is not tuple or not all(
            type(item) is VerifierIdentityBinding for item in self.verifier_identities
        ):
            raise ValueError("hard calibration verifier identities must be exact records")
        if (
            type(self.case_id_key_commitment) is not str
            or re.fullmatch(r"sha256:[0-9a-f]{64}", self.case_id_key_commitment) is None
        ):
            raise ValueError("hard calibration case-id key commitment is not canonical")
        if not hmac.compare_digest(
            _case_id_key_commitment(self.case_id_key),
            self.case_id_key_commitment,
        ):
            raise ValueError("hard calibration case-id key differs from its commitment")
        self.corpus.validate_against(self.plan)
        bank = generate_public_calibration_bank_v2()
        if len(bank) != CALIBRATION_CASE_COUNT_V2:
            raise ValueError("hard calibration bank differs from its sealed case count")
        expected_identities = _verifier_identities(bank)
        if self.verifier_identities != expected_identities:
            raise ValueError("hard calibration verifier identity map differs from generator")
        expected_plan = _plan_for(
            bank,
            expected_identities,
            self.case_id_key_commitment,
        )
        if self.plan != expected_plan or not hmac.compare_digest(
            self.plan.digest(),
            expected_plan.digest(),
        ):
            raise ValueError("hard calibration plan differs from current sealed generator")
        for candidate in self.corpus.candidates:
            expected_candidate = _candidate(
                bank[candidate.generator_counter],
                counter=candidate.generator_counter,
                plan=expected_plan,
                identities=expected_identities,
                case_id_key=self.case_id_key,
            )
            if candidate != expected_candidate:
                raise ValueError(
                    "hard calibration candidate differs from keyed generator reconstruction"
                )

    @property
    def commitment(self) -> str:
        self.__post_init__()
        return self.corpus.commitment

    def public_commitment_payload(self) -> dict[str, Any]:
        """Return the only safe pre-attempt publication view of this corpus."""

        self.__post_init__()
        return {
            "case_count": len(self.corpus.candidates),
            "case_id_key_commitment": self.case_id_key_commitment,
            "manifest_digest": self.corpus.manifest.digest(),
            "plan": self.plan.to_dict(),
            "plan_digest": self.plan.digest(),
            "preregistration_commitment": self.corpus.commitment,
            "schema": HARD_CALIBRATION_PUBLIC_COMMITMENT_SCHEMA,
            "schema_version": HARD_CALIBRATION_PUBLIC_COMMITMENT_SCHEMA_VERSION,
        }

    def public_commitment_digest(self) -> str:
        return content_digest_v2(self.public_commitment_payload())


def build_hard_calibration_preregistration_v3(
    selection_seed: str,
    *,
    case_id_key: bytes,
) -> HardCalibrationPreregistrationV3:
    """Generate, certify, render, ticket, and validate the fixed public bank."""

    if type(selection_seed) is not str or not selection_seed.strip():
        raise ValueError("hard calibration selection seed must be nonempty plain text")
    key_commitment = _case_id_key_commitment(case_id_key)
    cases = generate_public_calibration_bank_v2()
    if len(cases) != CALIBRATION_CASE_COUNT_V2:
        raise ValueError("hard calibration bank differs from its sealed case count")
    identities = _verifier_identities(cases)
    plan = _plan_for(cases, identities, key_commitment)
    candidates = tuple(
        _candidate(
            case,
            counter=counter,
            plan=plan,
            identities=identities,
            case_id_key=case_id_key,
        )
        for counter, case in enumerate(cases)
    )
    corpus = build_preregistered_corpus(plan, selection_seed, candidates)
    return HardCalibrationPreregistrationV3(
        plan,
        corpus,
        identities,
        key_commitment,
        case_id_key,
    )


def _outcome_case(
    candidate: PreregistrationCandidate,
    case: HardChallengeCaseV2,
    *,
    brain_off_correct: bool,
    case_id_key: bytes,
) -> CalibrationCase:
    report = certification_report_v2(case)
    if not report.passed:
        raise ValueError("hard outcome case failed exact post-outcome verifier replay")
    expected_fields = {
        "domain_label": case.domain,
        "family_label": case.calibration_family,
        "fault_label": case.mechanism,
        "fault_line": case.faulty_line,
        "faulty_trace": case.trace(faulty=True),
        "generator_cell_label": case.generator_cell,
        "prompt": case.prompt,
        "template_label": case.template_id,
    }
    for field_name, expected in expected_fields.items():
        if getattr(candidate, field_name) != expected:
            raise ValueError("hard outcome case differs from its preregistered rendering")
    if candidate.stable_case_id != _opaque_stable_case_id(case, case_id_key):
        raise ValueError("hard outcome case differs from its keyed preregistered identity")
    semantic_digest = content_digest_v2(case.semantic_program.to_dict())
    if not hmac.compare_digest(candidate.semantic_program_digest, semantic_digest):
        raise ValueError("hard outcome case differs from its preregistered semantic program")
    executed = tuple(sorted((case.primary_oracle_digest, case.independent_oracle_digest)))
    if candidate.executed_verifier_implementation_digests != executed:
        raise ValueError("hard outcome verifier pair differs from preregistration")

    values: dict[str, Any] = {
        "brain_off_correct": brain_off_correct,
        "case_id": candidate.stable_case_id,
        "domain": candidate.domain_label,
        "eligible_fault_lines": candidate.eligible_fault_lines,
        "family": candidate.family_label,
        "fault_kind": candidate.fault_label,
        "fault_line": candidate.fault_line,
        "faulty_trace": candidate.faulty_trace,
        "generator_cell": candidate.generator_cell_label,
        "prompt": candidate.prompt,
        "template_id": candidate.template_label,
    }
    binding_digest = CalibrationCase.binding_digest_for(**values)
    identity_by_digest = {
        identity.implementation_digest: identity.verifier_id
        for identity in candidate.verifier_identities
    }
    receipts = tuple(
        OracleReceipt.issue(
            case_binding_digest=binding_digest,
            verifier_id=identity_by_digest[digest],
            first_invalid_line=case.faulty_line,
        )
        for digest in candidate.executed_verifier_implementation_digests
    )
    return CalibrationCase.build(**values, oracles=receipts)


def bind_hard_calibration_outcomes_v3(
    preregistration: HardCalibrationPreregistrationV3,
    brain_off_outcomes: Mapping[str, bool],
) -> CalibrationOutcomeBinding:
    """Bind one exact boolean outcome per opaque preregistered case identifier."""

    if type(preregistration) is not HardCalibrationPreregistrationV3:
        raise ValueError("hard outcome binding requires an exact preregistration")
    preregistration.__post_init__()
    if type(brain_off_outcomes) is not dict or not all(
        type(case_id) is str and type(value) is bool
        for case_id, value in brain_off_outcomes.items()
    ):
        raise ValueError("hard calibration outcomes must be an exact string-to-boolean object")
    expected_ids = {candidate.stable_case_id for candidate in preregistration.corpus.candidates}
    if set(brain_off_outcomes) != expected_ids:
        raise ValueError("hard calibration outcomes must cover the preregistered corpus exactly")

    bank = generate_public_calibration_bank_v2()
    cases = tuple(
        _outcome_case(
            candidate,
            bank[candidate.generator_counter],
            brain_off_correct=brain_off_outcomes[candidate.stable_case_id],
            case_id_key=preregistration.case_id_key,
        )
        for candidate in preregistration.corpus.candidates
    )
    if (
        protocol_module.bind_calibration_outcomes is not _PINNED_OUTCOME_BINDER
        or protocol_module.verify_calibration_outcome_binding is not _PINNED_OUTCOME_VERIFIER
    ):
        raise ValueError("hard outcome protocol callback changed after import")
    binding = _PINNED_OUTCOME_BINDER(
        preregistration.plan,
        preregistration.corpus,
        cases,
        preregistration.verifier_identities,
    )
    if type(binding) is not CalibrationOutcomeBinding:
        raise ValueError("hard outcome binder returned a noncanonical artifact")
    rebuilt = CalibrationOutcomeBinding.from_dict(binding.to_dict())
    if rebuilt != binding:
        raise ValueError("hard outcome binder returned a stale artifact")
    _PINNED_OUTCOME_VERIFIER(
        rebuilt,
        preregistration.plan,
        preregistration.corpus,
        cases,
        preregistration.verifier_identities,
    )
    return rebuilt


__all__ = [
    "HARD_CALIBRATION_CASE_ID_KEY_BYTES",
    "HARD_CALIBRATION_LAYOUT_LABEL",
    "HARD_CALIBRATION_PUBLIC_COMMITMENT_SCHEMA",
    "HARD_CALIBRATION_PUBLIC_COMMITMENT_SCHEMA_VERSION",
    "HARD_CALIBRATION_V3_SCHEMA",
    "HARD_CALIBRATION_V3_SCHEMA_VERSION",
    "HardCalibrationPreregistrationV3",
    "bind_hard_calibration_outcomes_v3",
    "build_hard_calibration_preregistration_v3",
]
