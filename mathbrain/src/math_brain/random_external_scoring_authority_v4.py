"""Derived-only authenticated scoring for the random-external v4 pilot.

The authority in this module never accepts a caller-authored outcome, response, prompt
digest, runtime-eligibility flag, one-start flag, or model-identity flag.  It consumes the
exact one-shot execution object, cold-replays its sealed plan and task map, reads immutable
``AttemptLedger`` history, reconstructs the stored ``CodexRunResult``, reruns the relevant
gold authority, deterministically scores the response, and signs that derived record.

The signing key is role-separated from every legacy v4 receipt key.  Both the pre-run
authority certificate and final scores use canonical Ed25519 framing under the single
dedicated domain ``org.math-research-brain/random-external-authenticated-score/v4``;
their exact schemas prevent cross-record substitution.
"""

from __future__ import annotations

import hashlib
import hmac
import importlib
import inspect
import re
from dataclasses import dataclass, field, replace
from enum import StrEnum
from pathlib import Path

from .attempt_ledger import (
    OutcomeKind,
    StartedAttempt,
    TerminalTransition,
)
from .canonical_ed25519_v3 import CanonicalEd25519SignerV3, CanonicalEd25519VerifierV3
from .codex_artifact_io import CodexArtifactError, deserialize_codex_run_result
from .codex_runner import CodexRunResult
from .exact_answer_v3 import ExactRationalV3
from .external_blind_gold_audit_v4 import (
    EXTERNAL_GOLD_REPORT_SIGNING_DOMAIN_V4,
    ExternalGoldAuditReportV4,
    ExternalGoldAuditStatusV4,
    _fresh_random_external_replay_v4,
    verify_external_gold_audit_report_v4,
)
from .github_precommit_anchor_v4 import verify_live_github_precommit_anchor_v4
from .mathnet_random_external_lane_v4 import (
    rebuild_mathnet_random_external_gold_vault_record_v4,
)
from .models import canonical_json
from .random_external_pilot_execution_v4 import (
    AcknowledgedRandomExternalExecutionV4,
    BoundRandomExternalExecutionV4,
    PreparedRandomExternalExecutionSlotV4,
    PreparedRandomExternalExecutionV4,
    RandomExternalClosedAttemptV4,
    RandomExternalClosedDispositionV4,
    RandomExternalExecutionClosureV4,
    RandomExternalExecutionPolicyV4,
    closed_artifacts_for_random_external_scoring_v4,
    random_external_solver_output_schema_v4,
)
from .random_external_pilot_task_map_v4 import (
    solver_projection_from_random_external_task_map_v4,
)
from .three_arm_evaluation import ProblemSource
from .three_arm_pilot_protocol_v3 import (
    EXACT_RATIONAL_PARSER_SOURCE_ARTIFACT_DIGEST_V3,
    EXACT_RATIONAL_SCORER_MANIFEST_DIGEST_V3,
    EXACT_RATIONAL_SCORING_RULE_DIGEST_V3,
    ArmBlindScoreCapsuleV3,
    ScoreDispositionV3,
    ScoreResultEvidenceV3,
    assert_three_arm_protocol_config_v3,
    render_solver_prompt_v3,
    score_arm_blind_capsule_v3,
    solver_prompt_digest_v3,
)
from .three_arm_random_external_pilot_v4 import (
    RANDOM_EXTERNAL_PILOT_TASK_COUNT_V4,
    RandomExternalPilotPlanV4,
)

RANDOM_EXTERNAL_AUTHENTICATED_SCORE_SCHEMA_V4 = (
    "org.math-research-brain/random-external-authenticated-score/v4"
)
RANDOM_EXTERNAL_AUTHENTICATED_SCORE_SIGNING_DOMAIN_V4 = (
    "org.math-research-brain/random-external-authenticated-score/v4"
)
RANDOM_EXTERNAL_SCORING_PRECOMMITMENT_SCHEMA_V4 = (
    "org.math-research-brain/random-external-authenticated-score-precommitment/v4"
)
RANDOM_EXTERNAL_SCORING_AUTHORIZATION_REPLAY_SCHEMA_V4 = (
    "org.math-research-brain/random-external-scoring-authorization-replay/v4"
)
RANDOM_EXTERNAL_CODE_MANIFEST_SCHEMA_V4 = (
    "org.math-research-brain/random-external-scoring-code-manifest/v4"
)
RANDOM_EXTERNAL_SOLVER_ARTIFACT_MEDIA_TYPE_V4 = (
    "application/vnd.math-research-brain.codex-run-result+json"
)

_DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}\Z", re.ASCII)
_SIGNATURE_RE = re.compile(r"ed25519:[0-9a-f]{128}\Z", re.ASCII)
_TASK_ID_RE = re.compile(r"solvertaskv3_[0-9a-f]{64}\Z", re.ASCII)
_SCORE_ID_RE = re.compile(r"scorev3_[0-9a-f]{64}\Z", re.ASCII)
_MAX_REPLAY_RECORD_BYTES_V4 = 64 * 1024 * 1024

_INTERNAL_VERIFY_PARAMETER_NAMES_V4 = (
    "report",
    "precommitment",
    "author_outputs",
    "author_commitments",
    "author_commitment_key",
    "body_a",
    "body_b",
    "body_a_evidence",
    "body_b_evidence",
    "gold_a",
    "gold_b",
    "gold_a_evidence",
    "gold_b_evidence",
    "relevance_a",
    "relevance_b",
    "relevance_a_evidence",
    "relevance_b_evidence",
    "card_projection",
    "external_selection",
    "external_precommitment_bytes",
    "external_beacon_receipt_bytes",
    "external_gold_audit_report",
    "expected_external_gold_report_verifier",
    "expected_external_gold_audit_signed_report_digest",
    "expected_external_selection_precommit_anchor_evidence_digest",
    "expected_external_precommitment_record_sha256",
    "expected_external_beacon_receipt_record_sha256",
    "external_source",
    "expected_external_selection_digest",
    "expected_external_projection_digest",
    "expected_external_random_lane_capability_digest",
    "duplicate_edge_key",
    "duplicate_edges",
    "duplicate_a",
    "duplicate_b",
    "duplicate_a_evidence",
    "duplicate_b_evidence",
    "author_execution",
    "expected_author_execution_manifest_digest",
    "expected_author_execution_collector_public_key",
    "audit_precommitment",
    "expected_audit_precommitment_digest",
    "evidence_key",
)
_INTERNAL_VERIFY_KWARGS_V4 = frozenset(_INTERNAL_VERIFY_PARAMETER_NAMES_V4[2:])


def _sha256(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _digest_json(value: object) -> str:
    return _sha256(canonical_json(value).encode("utf-8"))


def _domain_digest(value: object, *, domain: str) -> str:
    return _sha256(
        b"org.math-research-brain/random-external-scoring-derived-digest/v4\x00"
        + domain.encode("ascii")
        + b"\x00"
        + canonical_json(value).encode("utf-8")
    )


def _digest(value: object, label: str) -> str:
    if type(value) is not str or _DIGEST_RE.fullmatch(value) is None:
        raise ValueError(f"{label} must be a canonical SHA-256 digest")
    return value


def _signature(value: object, label: str) -> str:
    if type(value) is not str or _SIGNATURE_RE.fullmatch(value) is None:
        raise ValueError(f"{label} must be a canonical Ed25519 signature")
    return value


def _text(value: object, label: str, *, maximum: int) -> str:
    if type(value) is not str or not value or "\x00" in value:
        raise ValueError(f"{label} must be nonempty exact text")
    try:
        raw = value.encode("utf-8")
    except UnicodeEncodeError as error:
        raise ValueError(f"{label} must be valid UTF-8") from error
    if len(raw) > maximum:
        raise ValueError(f"{label} exceeds its byte budget")
    return value


def _repository_root(value: str | Path) -> Path:
    if type(value) is not str and not isinstance(value, Path):
        raise ValueError("scoring repository root must be an exact path")
    path = Path(value)
    if not path.is_absolute():
        raise ValueError("scoring repository root must be absolute")
    try:
        resolved = path.resolve(strict=True)
    except OSError as error:
        raise ValueError("scoring repository root is unavailable") from error
    if not resolved.is_dir() or not (resolved / ".git").exists():
        raise ValueError("scoring repository root must be an exact Git worktree")
    return resolved


def _fixed_verifier(
    verifier: CanonicalEd25519VerifierV3,
    *,
    domain: str,
    label: str,
) -> CanonicalEd25519VerifierV3:
    if type(verifier) is not CanonicalEd25519VerifierV3:
        raise ValueError(f"{label} must be an exact canonical Ed25519 verifier")
    rebuilt = CanonicalEd25519VerifierV3(verifier.public_key_bytes(), domain=domain)
    if not hmac.compare_digest(verifier.authority_commitment, rebuilt.authority_commitment):
        raise ValueError(f"{label} uses another signing domain")
    return rebuilt


def _verify_execution_live_anchor_v4(prepared: PreparedRandomExternalExecutionV4) -> str:
    if type(prepared) is not PreparedRandomExternalExecutionV4:
        raise ValueError("live-anchor replay requires exact prepared execution")
    prepared.__post_init__()
    evidence = prepared.anchor_evidence
    fresh = verify_live_github_precommit_anchor_v4(
        prepared.random_selection_precommitment_bytes,
        expected_precommitment_record_sha256=(
            prepared.expected_random_selection_precommitment_record_sha256
        ),
        expected_anchor_evidence_digest=evidence.content_digest,
        repository_owner=evidence.repository_owner,
        repository_name=evidence.repository_name,
        issue_number=evidence.issue_number,
        comment_id=evidence.comment_id,
        expected_actor_login=evidence.actor_login,
        expected_actor_id=evidence.actor_id,
    )
    if fresh != evidence:
        raise ValueError("live GitHub anchor differs from exact prepared execution evidence")
    return fresh.content_digest


def _critical_loaded_code_digest_v4(policy: RandomExternalExecutionPolicyV4) -> str:
    """Hash exact source artifacts in addition to the execution implementation root."""

    if type(policy) is not RandomExternalExecutionPolicyV4:
        raise ValueError("scoring code manifest requires exact execution policy")
    policy.__post_init__()
    modules = (
        ("scoring_authority", __name__),
        ("execution", "math_brain.random_external_pilot_execution_v4"),
        ("task_map", "math_brain.random_external_pilot_task_map_v4"),
        ("solver_protocol", "math_brain.three_arm_pilot_protocol_v3"),
        ("external_gold", "math_brain.external_blind_gold_audit_v4"),
        ("external_selector", "math_brain.mathnet_random_external_lane_v4"),
        ("internal_selector", "math_brain.internal_pilot_selection_v3"),
    )
    records: list[dict[str, str]] = []
    for label, module_name in modules:
        module = importlib.import_module(module_name)
        raw_path = getattr(module, "__file__", None)
        if type(raw_path) is not str:
            raise ValueError(f"{label} loaded source path is unavailable")
        path = Path(raw_path)
        if path.suffix != ".py" or not path.is_file() or path.is_symlink():
            raise ValueError(f"{label} loaded source is not an exact regular .py file")
        try:
            raw = path.read_bytes()
        except OSError as error:
            raise ValueError(f"{label} loaded source cannot be read") from error
        if not raw:
            raise ValueError(f"{label} loaded source is empty")
        records.append({"label": label, "source_sha256": _sha256(raw)})
    return _domain_digest(
        {
            "execution_implementation_manifest_digest": (
                policy.implementation_manifest_digest
            ),
            "parser_source_artifact_digest": (
                EXACT_RATIONAL_PARSER_SOURCE_ARTIFACT_DIGEST_V3
            ),
            "schema": RANDOM_EXTERNAL_CODE_MANIFEST_SCHEMA_V4,
            "source_artifacts": records,
        },
        domain="loaded-scoring-code-manifest-v4",
    )


class AttemptOutcomeV4(StrEnum):
    CORRECT = "correct"
    INCORRECT = "incorrect"
    SOLVER_ABSTENTION = "solver_abstention"
    INVALID_OUTPUT = "invalid_output"
    PARSER_FAILURE = "parser_failure"
    RUNNER_FAILURE = "runner_failure"

    @property
    def score(self) -> int:
        return int(self is AttemptOutcomeV4.CORRECT)


# Evaluation-facing name for the exact execution-owned closure enum.  This is an alias,
# not a nominal compatibility type: callers cannot substitute another disposition class.
ClosedAttemptDispositionV4 = RandomExternalClosedDispositionV4


@dataclass(frozen=True, slots=True)
class ExternalGoldScoringReplayInputsV4:
    random_precommitment_bytes: bytes = field(repr=False)
    random_beacon_receipt_bytes: bytes = field(repr=False)
    expected_precommitment_record_sha256: str
    expected_beacon_receipt_record_sha256: str
    source: object = field(repr=False)
    expected_capability: object = field(repr=False)
    expected_audit_input: object = field(repr=False)

    def __post_init__(self) -> None:
        if type(self) is not ExternalGoldScoringReplayInputsV4:
            raise ValueError("external replay-input subclasses are forbidden")
        for raw, label in (
            (self.random_precommitment_bytes, "random precommitment"),
            (self.random_beacon_receipt_bytes, "random beacon receipt"),
        ):
            if type(raw) is not bytes or not 1 <= len(raw) <= _MAX_REPLAY_RECORD_BYTES_V4:
                raise ValueError(f"{label} bytes are invalid")
        _digest(self.expected_precommitment_record_sha256, "random precommitment record")
        _digest(self.expected_beacon_receipt_record_sha256, "random beacon record")
        if not hmac.compare_digest(
            _sha256(self.random_precommitment_bytes),
            self.expected_precommitment_record_sha256,
        ) or not hmac.compare_digest(
            _sha256(self.random_beacon_receipt_bytes),
            self.expected_beacon_receipt_record_sha256,
        ):
            raise ValueError("external replay bytes differ from their exact record SHA-256")


@dataclass(frozen=True, slots=True)
class InternalGoldScoringReplayInputsV4:
    report: object = field(repr=False)
    precommitment: object = field(repr=False)
    verifier_kwargs: dict[str, object] = field(repr=False)

    def __post_init__(self) -> None:
        if type(self) is not InternalGoldScoringReplayInputsV4:
            raise ValueError("internal replay-input subclasses are forbidden")
        if type(self.verifier_kwargs) is not dict or frozenset(self.verifier_kwargs) != (
            _INTERNAL_VERIFY_KWARGS_V4
        ):
            raise ValueError("internal replay inputs must match the final exact verifier API")


@dataclass(frozen=True, slots=True)
class _ExternalGoldReplayResultV4:
    answers: dict[str, ExactRationalV3] = field(repr=False)
    case_commitments: dict[str, str] = field(repr=False)
    report_authority_commitment: str
    signed_report_digest: str
    random_replay_evidence_digest: str
    random_lane_capability_digest: str
    random_gold_audit_input_digest: str
    private_gold_vault_record_sha256: str
    live_anchor_evidence_digest: str


@dataclass(frozen=True, slots=True)
class _InternalGoldReplayResultV4:
    answers: dict[str, ExactRationalV3] = field(repr=False)
    case_commitments: dict[str, str] = field(repr=False)
    author_precommitment_digest: str
    selection_digest: str
    selection_private_record_digest: str
    audit_precommitment_digest: str
    author_execution_manifest_digest: str


def _external_gold_replay_v4(
    plan: RandomExternalPilotPlanV4,
    prepared: PreparedRandomExternalExecutionV4,
    report: ExternalGoldAuditReportV4,
    expected_report_verifier: CanonicalEd25519VerifierV3,
    replay_inputs: ExternalGoldScoringReplayInputsV4,
) -> _ExternalGoldReplayResultV4:
    """Live-refetch chronology, verify the real report, and rebuild the private vault."""

    if (
        type(plan) is not RandomExternalPilotPlanV4
        or type(prepared) is not PreparedRandomExternalExecutionV4
        or type(report) is not ExternalGoldAuditReportV4
        or type(replay_inputs) is not ExternalGoldScoringReplayInputsV4
    ):
        raise ValueError("external gold scoring requires exact production artifacts")
    plan.__post_init__()
    prepared.__post_init__()
    replay_inputs.__post_init__()
    verifier = _fixed_verifier(
        expected_report_verifier,
        domain=EXTERNAL_GOLD_REPORT_SIGNING_DOMAIN_V4,
        label="external-gold report verifier",
    )
    if (
        replay_inputs.random_precommitment_bytes
        != prepared.random_selection_precommitment_bytes
        or replay_inputs.expected_precommitment_record_sha256
        != prepared.expected_random_selection_precommitment_record_sha256
    ):
        raise ValueError("external replay substitutes the exact prepared random precommitment")
    live_anchor_digest = _verify_execution_live_anchor_v4(prepared)
    verify_external_gold_audit_report_v4(
        report,
        verifier,
        expected_signed_report_digest=plan.config.external_gold_audit_signed_report_digest,
    )
    fresh = _fresh_random_external_replay_v4(
        replay_inputs.random_precommitment_bytes,
        replay_inputs.random_beacon_receipt_bytes,
        expected_precommitment_record_sha256=(
            replay_inputs.expected_precommitment_record_sha256
        ),
        expected_beacon_receipt_record_sha256=(
            replay_inputs.expected_beacon_receipt_record_sha256
        ),
        external_selection_precommit_anchor_evidence_digest=live_anchor_digest,
        source=replay_inputs.source,
        expected_capability=replay_inputs.expected_capability,
        expected_audit_input=replay_inputs.expected_audit_input,
    )
    if fresh.evidence != report.random_replay_evidence:
        raise ValueError("signed external-gold report differs from fresh official replay")
    evidence = fresh.evidence
    evidence.validate_current()
    selection = evidence.selection
    if (
        evidence.precommitment_record_sha256
        != prepared.expected_random_selection_precommitment_record_sha256
        or evidence.beacon_receipt_record_sha256
        != replay_inputs.expected_beacon_receipt_record_sha256
        or evidence.external_selection_precommit_anchor_evidence_digest != live_anchor_digest
        or live_anchor_digest
        != plan.config.external_selection_precommit_anchor_evidence_digest
        or selection.random_lane_capability_digest
        != plan.config.external_selection_capability_digest
        or report.random_lane_capability_digest
        != plan.config.external_selection_capability_digest
        or report.random_gold_audit_input_digest != selection.random_gold_audit_input_digest
        or report.private_gold_vault_record_sha256
        != selection.private_gold_vault_record_sha256
    ):
        raise ValueError("external gold replay differs from plan/execution commitments")
    rebuilt_vault = rebuild_mathnet_random_external_gold_vault_record_v4(
        replay_inputs.random_precommitment_bytes,
        replay_inputs.random_beacon_receipt_bytes,
        expected_precommitment_record_sha256=(
            replay_inputs.expected_precommitment_record_sha256
        ),
        expected_beacon_receipt_record_sha256=(
            replay_inputs.expected_beacon_receipt_record_sha256
        ),
        source=replay_inputs.source,
        capability=fresh.capability,
        gold_input=fresh.audit_input,
    )
    if type(rebuilt_vault) is not bytes or not hmac.compare_digest(
        _sha256(rebuilt_vault), report.private_gold_vault_record_sha256
    ):
        raise ValueError("external report differs from real private-vault replay")
    if (
        report.status is not ExternalGoldAuditStatusV4.FEASIBLE
        or report.admitted_count != 12
        or len(report.admitted) != 12
        or report.failure_codes
    ):
        raise ValueError("external gold scoring requires one feasible 12-of-12 report")
    external_cases = tuple(case for case in plan.cases if case.source is ProblemSource.EXTERNAL)
    by_id = {item.item_id: item for item in report.admitted}
    if len(external_cases) != 12 or set(by_id) != {case.case_id for case in external_cases}:
        raise ValueError("external gold report substitutes the plan case universe")
    answers: dict[str, ExactRationalV3] = {}
    commitments: dict[str, str] = {}
    for case in external_cases:
        item = by_id[case.case_id]
        item.__post_init__()
        if (
            item.problem_text != case.problem_text
            or _sha256(item.problem_text.encode("utf-8")) != case.problem_digest
            or type(item.source_answer) is not ExactRationalV3
        ):
            raise ValueError("external gold report substitutes a problem or exact answer")
        item.source_answer.validate_current()
        answers[case.case_id] = item.source_answer
        commitments[case.case_id] = item.gold_commitment
    return _ExternalGoldReplayResultV4(
        answers=answers,
        case_commitments=commitments,
        report_authority_commitment=report.report_signing_authority_commitment,
        signed_report_digest=report.signed_report_digest,
        random_replay_evidence_digest=evidence.content_digest,
        random_lane_capability_digest=selection.random_lane_capability_digest,
        random_gold_audit_input_digest=selection.random_gold_audit_input_digest,
        private_gold_vault_record_sha256=selection.private_gold_vault_record_sha256,
        live_anchor_evidence_digest=live_anchor_digest,
    )


def _internal_gold_replay_v4(
    plan: RandomExternalPilotPlanV4,
    replay_inputs: InternalGoldScoringReplayInputsV4,
) -> _InternalGoldReplayResultV4:
    """Invoke the final hardened verifier through one exact, fail-closed adapter."""

    if type(replay_inputs) is not InternalGoldScoringReplayInputsV4:
        raise ValueError("internal gold scoring requires exact replay inputs")
    replay_inputs.__post_init__()
    module = importlib.import_module("math_brain.internal_pilot_selection_v3")
    try:
        report_type = module.InternalPilotSelectionReportV3
        precommitment_type = module.InternalAuthorPrecommitmentV3
        status_type = module.InternalPilotSelectionStatusV3
        verify = module.verify_internal_pilot_selection_v3
    except AttributeError as error:
        raise ValueError("final hardened internal selector API is unavailable") from error
    signature = inspect.signature(verify)
    if tuple(signature.parameters) != _INTERNAL_VERIFY_PARAMETER_NAMES_V4:
        raise ValueError("internal selector verifier signature changed; scoring fails closed")
    parameters = tuple(signature.parameters.values())
    if any(
        parameter.kind is not inspect.Parameter.KEYWORD_ONLY for parameter in parameters[2:]
    ):
        raise ValueError("internal selector verifier lost its keyword-only authority surface")
    if type(replay_inputs.report) is not report_type or type(replay_inputs.precommitment) is not (
        precommitment_type
    ):
        raise ValueError("internal scoring rejects nominal or legacy selector artifacts")
    kwargs = dict(replay_inputs.verifier_kwargs)
    if frozenset(kwargs) != _INTERNAL_VERIFY_KWARGS_V4:
        raise ValueError("internal selector replay inputs changed during snapshot")
    verify(replay_inputs.report, replay_inputs.precommitment, **kwargs)
    report = replay_inputs.report
    if (
        report.status is not status_type.FEASIBLE
        or report.selected_count != 12
        or len(report.selected) != 12
    ):
        raise ValueError("internal gold scoring requires one feasible 12-of-12 selection")
    selection_digest = _digest(report.selection_digest, "internal selection digest")
    if selection_digest != plan.config.internal_selection_capability_digest:
        raise ValueError("internal selection differs from v4 plan capability")
    if not hasattr(replay_inputs.precommitment, "to_dict"):
        raise ValueError("internal author precommitment lacks its exact projection")
    author_precommitment_digest = _digest_json(replay_inputs.precommitment.to_dict())
    if author_precommitment_digest != plan.config.internal_selection_precommitment_digest:
        raise ValueError("internal author precommitment differs from v4 plan")
    selection_private_record_digest = _digest_json(report.to_private_dict())
    audit_precommitment_digest = _digest(
        kwargs["expected_audit_precommitment_digest"],
        "expected internal audit precommitment",
    )
    author_execution_manifest_digest = _digest(
        kwargs["expected_author_execution_manifest_digest"],
        "expected internal author-execution manifest",
    )
    internal_cases = tuple(case for case in plan.cases if case.source is ProblemSource.INTERNAL)
    selected = {item.candidate_id: item for item in report.selected}
    author_outputs = kwargs["author_outputs"]
    if (
        len(internal_cases) != 12
        or set(selected) != {case.case_id for case in internal_cases}
        or type(author_outputs) is not tuple
    ):
        raise ValueError("internal selector substitutes the exact plan/output universe")
    outputs = {item.candidate_id: item for item in author_outputs}
    answers: dict[str, ExactRationalV3] = {}
    commitments: dict[str, str] = {}
    for case in internal_cases:
        selected_case = selected[case.case_id]
        output = outputs.get(case.case_id)
        if (
            selected_case.problem_digest != case.problem_digest
            or output is None
            or type(output.author_answer) is not ExactRationalV3
        ):
            raise ValueError("internal selected case lacks exact replayed source gold")
        output.__post_init__()
        if output.problem_text != case.problem_text or output.problem_digest != case.problem_digest:
            raise ValueError("internal author output substitutes a plan problem")
        output.author_answer.validate_current()
        answers[case.case_id] = output.author_answer
        commitments[case.case_id] = selected_case.gold_commitment
    return _InternalGoldReplayResultV4(
        answers=answers,
        case_commitments=commitments,
        author_precommitment_digest=author_precommitment_digest,
        selection_digest=selection_digest,
        selection_private_record_digest=selection_private_record_digest,
        audit_precommitment_digest=audit_precommitment_digest,
        author_execution_manifest_digest=author_execution_manifest_digest,
    )


@dataclass(frozen=True, slots=True)
class RandomExternalScoringPrecommitmentV4:
    """Signed pre-run certificate for the exact authority, code, gold, and ledger roots."""

    plan_digest: str
    plan_public_precommitment_digest: str
    task_map_public_precommitment_digest: str
    authenticated_score_authority_commitment: str
    execution_precommitment_digest: str
    execution_public_record_digest: str
    execution_implementation_manifest_digest: str
    ledger_precommitment_digest: str
    runtime_policy_digest: str
    loaded_code_digest: str
    external_gold_report_authority_commitment: str
    external_gold_signed_report_digest: str
    external_random_replay_evidence_digest: str
    external_random_precommitment_record_sha256: str
    external_random_beacon_receipt_record_sha256: str
    external_selection_precommit_anchor_evidence_digest: str
    external_random_lane_capability_digest: str
    external_random_gold_audit_input_digest: str
    external_private_gold_vault_record_sha256: str
    internal_author_precommitment_digest: str
    internal_selection_digest: str
    internal_selection_private_record_digest: str
    internal_audit_precommitment_digest: str
    internal_author_execution_manifest_digest: str
    parser_digest: str = EXACT_RATIONAL_PARSER_SOURCE_ARTIFACT_DIGEST_V3
    scorer_manifest_digest: str = EXACT_RATIONAL_SCORER_MANIFEST_DIGEST_V3
    scoring_rule_digest: str = EXACT_RATIONAL_SCORING_RULE_DIGEST_V3
    model_identity_verified: bool = False
    caller_outcome_authorized: bool = False
    caller_runtime_flags_authorized: bool = False
    authority_signature: str = field(default="", repr=False)

    def __post_init__(self) -> None:
        if type(self) is not RandomExternalScoringPrecommitmentV4:
            raise ValueError("scoring-precommitment subclasses are forbidden")
        for value, label in (
            (self.plan_digest, "scoring-precommitment plan"),
            (self.plan_public_precommitment_digest, "scoring-precommitment public plan"),
            (self.task_map_public_precommitment_digest, "scoring-precommitment task map"),
            (self.authenticated_score_authority_commitment, "authenticated-score authority"),
            (self.execution_precommitment_digest, "execution precommitment"),
            (self.execution_public_record_digest, "execution public record"),
            (
                self.execution_implementation_manifest_digest,
                "execution implementation manifest",
            ),
            (self.ledger_precommitment_digest, "attempt-ledger precommitment"),
            (self.runtime_policy_digest, "runtime policy"),
            (self.loaded_code_digest, "loaded scoring code"),
            (self.external_gold_report_authority_commitment, "external-gold authority"),
            (self.external_gold_signed_report_digest, "signed external-gold report"),
            (self.external_random_replay_evidence_digest, "external random replay"),
            (self.external_random_precommitment_record_sha256, "external precommit record"),
            (self.external_random_beacon_receipt_record_sha256, "external beacon record"),
            (
                self.external_selection_precommit_anchor_evidence_digest,
                "external live-anchor evidence",
            ),
            (self.external_random_lane_capability_digest, "external lane capability"),
            (self.external_random_gold_audit_input_digest, "external gold input"),
            (self.external_private_gold_vault_record_sha256, "external private vault"),
            (self.internal_author_precommitment_digest, "internal author precommitment"),
            (self.internal_selection_digest, "internal selection"),
            (self.internal_selection_private_record_digest, "internal private selection"),
            (self.internal_audit_precommitment_digest, "internal audit precommitment"),
            (
                self.internal_author_execution_manifest_digest,
                "internal author-execution manifest",
            ),
            (self.parser_digest, "parser source"),
            (self.scorer_manifest_digest, "scorer manifest"),
            (self.scoring_rule_digest, "scoring rule"),
        ):
            _digest(value, label)
        if (
            self.parser_digest != EXACT_RATIONAL_PARSER_SOURCE_ARTIFACT_DIGEST_V3
            or self.scorer_manifest_digest != EXACT_RATIONAL_SCORER_MANIFEST_DIGEST_V3
            or self.scoring_rule_digest != EXACT_RATIONAL_SCORING_RULE_DIGEST_V3
        ):
            raise ValueError("scoring precommitment uses another v3 parser/scorer contract")
        if (
            self.model_identity_verified is not False
            or self.caller_outcome_authorized is not False
            or self.caller_runtime_flags_authorized is not False
        ):
            raise ValueError("scoring precommitment delegates forbidden caller authority")
        _signature(self.authority_signature, "scoring-precommitment authority signature")

    def unsigned_record(self) -> dict[str, object]:
        return {
            "authenticated_score_authority_commitment": (
                self.authenticated_score_authority_commitment
            ),
            "caller_outcome_authorized": False,
            "caller_runtime_flags_authorized": False,
            "execution_implementation_manifest_digest": (
                self.execution_implementation_manifest_digest
            ),
            "execution_precommitment_digest": self.execution_precommitment_digest,
            "execution_public_record_digest": self.execution_public_record_digest,
            "external_gold_report_authority_commitment": (
                self.external_gold_report_authority_commitment
            ),
            "external_gold_signed_report_digest": self.external_gold_signed_report_digest,
            "external_private_gold_vault_record_sha256": (
                self.external_private_gold_vault_record_sha256
            ),
            "external_random_beacon_receipt_record_sha256": (
                self.external_random_beacon_receipt_record_sha256
            ),
            "external_random_gold_audit_input_digest": (
                self.external_random_gold_audit_input_digest
            ),
            "external_random_lane_capability_digest": (
                self.external_random_lane_capability_digest
            ),
            "external_random_precommitment_record_sha256": (
                self.external_random_precommitment_record_sha256
            ),
            "external_random_replay_evidence_digest": (
                self.external_random_replay_evidence_digest
            ),
            "external_selection_precommit_anchor_evidence_digest": (
                self.external_selection_precommit_anchor_evidence_digest
            ),
            "internal_audit_precommitment_digest": self.internal_audit_precommitment_digest,
            "internal_author_execution_manifest_digest": (
                self.internal_author_execution_manifest_digest
            ),
            "internal_author_precommitment_digest": self.internal_author_precommitment_digest,
            "internal_selection_digest": self.internal_selection_digest,
            "internal_selection_private_record_digest": (
                self.internal_selection_private_record_digest
            ),
            "ledger_precommitment_digest": self.ledger_precommitment_digest,
            "loaded_code_digest": self.loaded_code_digest,
            "model_identity_verified": False,
            "parser_digest": self.parser_digest,
            "plan_digest": self.plan_digest,
            "plan_public_precommitment_digest": self.plan_public_precommitment_digest,
            "runtime_policy_digest": self.runtime_policy_digest,
            "schema": RANDOM_EXTERNAL_SCORING_PRECOMMITMENT_SCHEMA_V4,
            "schema_version": 4,
            "scorer_manifest_digest": self.scorer_manifest_digest,
            "scoring_rule_digest": self.scoring_rule_digest,
            "task_map_public_precommitment_digest": self.task_map_public_precommitment_digest,
        }

    @property
    def signed_digest(self) -> str:
        self.__post_init__()
        return _domain_digest(
            {
                "authority_signature": self.authority_signature,
                "record": self.unsigned_record(),
            },
            domain="signed-scoring-precommitment-v4",
        )


@dataclass(frozen=True, slots=True)
class ScoringAuthorizationReplayV4:
    """Strict pre-STARTED replay result for the execution-authorization builder."""

    plan_digest: str
    authenticated_score_authority_commitment: str
    scoring_precommitment_digest: str
    execution_precommitment_digest: str
    execution_public_record_digest: str
    ledger_precommitment_digest: str
    runtime_policy_digest: str
    execution_implementation_manifest_digest: str
    loaded_code_digest: str
    content_digest: str = field(init=False)

    def __post_init__(self) -> None:
        if type(self) is not ScoringAuthorizationReplayV4:
            raise ValueError("scoring-authorization replay subclasses are forbidden")
        for value, label in (
            (self.plan_digest, "scoring replay plan"),
            (self.authenticated_score_authority_commitment, "scoring replay authority"),
            (self.scoring_precommitment_digest, "scoring replay signed certificate"),
            (self.execution_precommitment_digest, "scoring replay execution precommitment"),
            (self.execution_public_record_digest, "scoring replay execution record"),
            (self.ledger_precommitment_digest, "scoring replay ledger"),
            (self.runtime_policy_digest, "scoring replay runtime policy"),
            (
                self.execution_implementation_manifest_digest,
                "scoring replay implementation manifest",
            ),
            (self.loaded_code_digest, "scoring replay loaded code"),
        ):
            _digest(value, label)
        object.__setattr__(
            self,
            "content_digest",
            _domain_digest(self._payload(), domain="scoring-authorization-replay-v4"),
        )

    def _payload(self) -> dict[str, object]:
        return {
            "authenticated_score_authority_commitment": (
                self.authenticated_score_authority_commitment
            ),
            "execution_implementation_manifest_digest": (
                self.execution_implementation_manifest_digest
            ),
            "execution_precommitment_digest": self.execution_precommitment_digest,
            "execution_public_record_digest": self.execution_public_record_digest,
            "ledger_precommitment_digest": self.ledger_precommitment_digest,
            "loaded_code_digest": self.loaded_code_digest,
            "plan_digest": self.plan_digest,
            "runtime_policy_digest": self.runtime_policy_digest,
            "schema": RANDOM_EXTERNAL_SCORING_AUTHORIZATION_REPLAY_SCHEMA_V4,
            "schema_version": 4,
            "scoring_precommitment_digest": self.scoring_precommitment_digest,
        }

    def commitment_record(self) -> dict[str, object]:
        self.__post_init__()
        return self._payload() | {"content_digest": self.content_digest}


@dataclass(frozen=True, slots=True)
class AuthenticatedRandomExternalScoreV4:
    """Only production score type accepted by v4 evaluation and analysis."""

    plan_digest: str
    plan_public_precommitment_digest: str
    task_map_public_precommitment_digest: str
    scoring_precommitment_digest: str
    execution_public_record_digest: str
    ledger_precommitment_digest: str
    closed_attempt_manifest_digest: str
    ordinal: int
    schedule_index: int
    assignment_digest: str
    case_id: str
    source: ProblemSource
    task_id: str
    response_task_id: str
    score_id: str
    problem_digest: str
    context_digest: str
    prompt_digest: str
    started_record_digest: str
    terminal_record_digest: str
    terminal_record_present: bool
    closed_attempt_disposition: ClosedAttemptDispositionV4
    ledger_manifest_digest: str
    attempt_evidence_digest: str
    transcript_digest: str
    response_digest: str
    model_evidence_digest: str
    executable_digest: str
    loaded_code_digest: str
    parser_digest: str
    scorer_spec_digest: str
    scorer_manifest_digest: str
    scorer_evidence_digest: str
    runtime_policy_digest: str
    gold_authority_digest: str
    gold_case_binding_digest: str
    scoring_rule_digest: str
    score_result_digest: str | None
    parsed_output_digest: str | None
    submitted_answer: ExactRationalV3 | None
    parser_disposition: ScoreDispositionV3 | None
    outcome: AttemptOutcomeV4
    runtime_eligible: bool
    started_once_verified: bool
    served_model: str
    effort: str
    model_identity_verified: bool
    scorer_blinded: bool
    authority_signature: str = field(repr=False)

    def __post_init__(self) -> None:
        if type(self) is not AuthenticatedRandomExternalScoreV4:
            raise ValueError("authenticated-score subclasses are forbidden")
        for value, label in (
            (self.plan_digest, "score plan"),
            (self.plan_public_precommitment_digest, "score public plan"),
            (self.task_map_public_precommitment_digest, "score task map"),
            (self.scoring_precommitment_digest, "score authority precommitment"),
            (self.execution_public_record_digest, "score execution record"),
            (self.ledger_precommitment_digest, "score ledger precommitment"),
            (self.closed_attempt_manifest_digest, "score closed-attempt manifest"),
            (self.assignment_digest, "score assignment"),
            (self.task_id, "score private task"),
            (self.problem_digest, "score problem"),
            (self.context_digest, "score context"),
            (self.prompt_digest, "score prompt"),
            (self.started_record_digest, "score STARTED record"),
            (self.terminal_record_digest, "score terminal evidence"),
            (self.ledger_manifest_digest, "score ledger manifest"),
            (self.attempt_evidence_digest, "score attempt evidence"),
            (self.transcript_digest, "score transcript"),
            (self.response_digest, "score response"),
            (self.model_evidence_digest, "score model evidence"),
            (self.executable_digest, "score executable"),
            (self.loaded_code_digest, "score loaded code"),
            (self.parser_digest, "score parser"),
            (self.scorer_spec_digest, "score specification"),
            (self.scorer_manifest_digest, "score manifest"),
            (self.scorer_evidence_digest, "score evidence"),
            (self.runtime_policy_digest, "score runtime policy"),
            (self.gold_authority_digest, "score gold authority"),
            (self.gold_case_binding_digest, "score gold case binding"),
            (self.scoring_rule_digest, "score rule"),
        ):
            _digest(value, label)
        for value, label in (
            (self.score_result_digest, "score result"),
            (self.parsed_output_digest, "score parsed output"),
        ):
            if value is not None:
                _digest(value, label)
        if type(self.ordinal) is not int or type(self.schedule_index) is not int or (
            self.ordinal != self.schedule_index
            or not 0 <= self.ordinal < RANDOM_EXTERNAL_PILOT_TASK_COUNT_V4
        ):
            raise ValueError("authenticated-score ordinal/schedule identity is invalid")
        _text(self.case_id, "score case id", maximum=128)
        if type(self.source) is not ProblemSource:
            raise ValueError("authenticated-score source must be exact")
        if type(self.response_task_id) is not str or _TASK_ID_RE.fullmatch(
            self.response_task_id
        ) is None:
            raise ValueError("authenticated-score response task id is invalid")
        if type(self.score_id) is not str or _SCORE_ID_RE.fullmatch(self.score_id) is None:
            raise ValueError("authenticated-score score id is invalid")
        if type(self.closed_attempt_disposition) is not ClosedAttemptDispositionV4:
            raise ValueError("authenticated-score closure disposition must be exact")
        if type(self.terminal_record_present) is not bool or (
            self.terminal_record_present
            is (
                self.closed_attempt_disposition
                is ClosedAttemptDispositionV4.BURNED_RUNNER_FAILURE
            )
        ):
            raise ValueError("authenticated-score terminal-presence evidence is inconsistent")
        if not hmac.compare_digest(
            self.closed_attempt_manifest_digest,
            self.ledger_manifest_digest,
        ):
            raise ValueError("authenticated score must bind one exact execution closure")
        if type(self.outcome) is not AttemptOutcomeV4:
            raise ValueError("authenticated-score outcome must be exact")
        if (
            type(self.runtime_eligible) is not bool
            or self.started_once_verified is not True
            or self.model_identity_verified is not False
            or self.scorer_blinded is not True
        ):
            raise ValueError("authenticated-score derived integrity flags are invalid")
        _text(self.served_model, "score model label", maximum=128)
        _text(self.effort, "score effort", maximum=64)
        if self.parser_digest != EXACT_RATIONAL_PARSER_SOURCE_ARTIFACT_DIGEST_V3 or (
            self.scorer_manifest_digest != EXACT_RATIONAL_SCORER_MANIFEST_DIGEST_V3
        ) or self.scoring_rule_digest != EXACT_RATIONAL_SCORING_RULE_DIGEST_V3:
            raise ValueError("authenticated score uses another parser/scorer contract")
        if self.submitted_answer is not None:
            if type(self.submitted_answer) is not ExactRationalV3:
                raise ValueError("authenticated submitted answer must be exact rational")
            self.submitted_answer.validate_current()
        if self.parser_disposition is not None and type(self.parser_disposition) is not (
            ScoreDispositionV3
        ):
            raise ValueError("authenticated parser disposition must be exact")
        result_present = self.score_result_digest is not None
        if result_present != (self.parser_disposition is not None) or (
            self.parsed_output_digest is not None
            and self.parser_disposition is ScoreDispositionV3.MALFORMED
        ):
            raise ValueError("authenticated score-result fields are inconsistent")
        if self.outcome is AttemptOutcomeV4.RUNNER_FAILURE:
            if result_present or self.runtime_eligible:
                raise ValueError("runner failures cannot claim parser or runtime eligibility")
        elif not result_present:
            raise ValueError("solver outcomes require deterministic score evidence")
        if self.closed_attempt_disposition is not ClosedAttemptDispositionV4.RETURNED and (
            self.outcome is not AttemptOutcomeV4.RUNNER_FAILURE
        ):
            raise ValueError("non-returned ledger closures must derive runner failure")
        _signature(self.authority_signature, "authenticated-score authority signature")

    @property
    def correct(self) -> bool:
        return bool(self.outcome.score)

    def unsigned_record(self) -> dict[str, object]:
        self.__post_init__()
        return {
            "assignment_digest": self.assignment_digest,
            "attempt_evidence_digest": self.attempt_evidence_digest,
            "case_id": self.case_id,
            "closed_attempt_disposition": self.closed_attempt_disposition.value,
            "closed_attempt_manifest_digest": self.closed_attempt_manifest_digest,
            "context_digest": self.context_digest,
            "effort": self.effort,
            "executable_digest": self.executable_digest,
            "execution_public_record_digest": self.execution_public_record_digest,
            "gold_authority_digest": self.gold_authority_digest,
            "gold_case_binding_digest": self.gold_case_binding_digest,
            "ledger_manifest_digest": self.ledger_manifest_digest,
            "ledger_precommitment_digest": self.ledger_precommitment_digest,
            "loaded_code_digest": self.loaded_code_digest,
            "model_evidence_digest": self.model_evidence_digest,
            "model_identity_verified": False,
            "ordinal": self.ordinal,
            "outcome": self.outcome.value,
            "parsed_output_digest": self.parsed_output_digest,
            "parser_digest": self.parser_digest,
            "parser_disposition": (
                None if self.parser_disposition is None else self.parser_disposition.value
            ),
            "plan_digest": self.plan_digest,
            "plan_public_precommitment_digest": self.plan_public_precommitment_digest,
            "problem_digest": self.problem_digest,
            "prompt_digest": self.prompt_digest,
            "response_digest": self.response_digest,
            "response_task_id": self.response_task_id,
            "runtime_eligible": self.runtime_eligible,
            "runtime_policy_digest": self.runtime_policy_digest,
            "schedule_index": self.schedule_index,
            "schema": RANDOM_EXTERNAL_AUTHENTICATED_SCORE_SCHEMA_V4,
            "schema_version": 4,
            "score_id": self.score_id,
            "score_result_digest": self.score_result_digest,
            "scorer_blinded": True,
            "scorer_evidence_digest": self.scorer_evidence_digest,
            "scorer_manifest_digest": self.scorer_manifest_digest,
            "scorer_spec_digest": self.scorer_spec_digest,
            "scoring_precommitment_digest": self.scoring_precommitment_digest,
            "scoring_rule_digest": self.scoring_rule_digest,
            "served_model": self.served_model,
            "source": self.source.value,
            "started_once_verified": True,
            "started_record_digest": self.started_record_digest,
            "submitted_answer": (
                None if self.submitted_answer is None else self.submitted_answer.to_dict()
            ),
            "task_id": self.task_id,
            "task_map_public_precommitment_digest": (
                self.task_map_public_precommitment_digest
            ),
            "terminal_record_digest": self.terminal_record_digest,
            "terminal_record_present": self.terminal_record_present,
            "transcript_digest": self.transcript_digest,
        }

    def commitment_record(self) -> dict[str, object]:
        return {**self.unsigned_record(), "authority_signature": self.authority_signature}

    @property
    def signed_digest(self) -> str:
        return _domain_digest(self.commitment_record(), domain="signed-authenticated-score-v4")


class AuthenticatedRandomScoreVerifierV4:
    """Public-key-only verifier for the sole accepted v4 score type."""

    __slots__ = ("_verifier",)

    def __init__(self, public_key_bytes: bytes) -> None:
        if type(self) is not AuthenticatedRandomScoreVerifierV4:
            raise ValueError("authenticated-score verifier subclasses are forbidden")
        self._verifier = CanonicalEd25519VerifierV3(
            public_key_bytes,
            domain=RANDOM_EXTERNAL_AUTHENTICATED_SCORE_SIGNING_DOMAIN_V4,
        )

    @property
    def commitment(self) -> str:
        return self._verifier.authority_commitment

    def public_key_bytes(self) -> bytes:
        return self._verifier.public_key_bytes()

    def _verify_precommitment_roots(
        self,
        bound: BoundRandomExternalExecutionV4,
        precommitment: RandomExternalScoringPrecommitmentV4,
        *,
        repository_root: str | Path,
        expected_signed_digest: str,
    ) -> None:
        if (
            type(self) is not AuthenticatedRandomScoreVerifierV4
            or type(bound) is not BoundRandomExternalExecutionV4
            or type(precommitment) is not RandomExternalScoringPrecommitmentV4
        ):
            raise ValueError("scoring certificate verification requires exact artifacts")
        bound.__post_init__()
        precommitment.__post_init__()
        plan = bound.prepared.plan
        task_map = bound.prepared.task_map
        policy = bound.prepared.policy
        policy.validate_current(repository_root=_repository_root(repository_root))
        _verify_execution_live_anchor_v4(bound.prepared)
        assert_three_arm_protocol_config_v3(plan.config.base)
        expected = _digest(expected_signed_digest, "expected scoring precommitment")
        if not hmac.compare_digest(precommitment.signed_digest, expected):
            raise ValueError("scoring authority certificate differs from external expectation")
        if not all(
            (
                self.commitment == plan.config.authenticated_score_authority_commitment,
                precommitment.authenticated_score_authority_commitment == self.commitment,
                precommitment.plan_digest == plan.digest,
                precommitment.plan_public_precommitment_digest
                == plan.public_precommitment_digest,
                precommitment.task_map_public_precommitment_digest
                == task_map.public_precommitment_digest,
                precommitment.execution_precommitment_digest
                == bound.prepared.public_precommitment_digest,
                precommitment.execution_public_record_digest == bound.public_record_digest,
                precommitment.execution_implementation_manifest_digest
                == policy.implementation_manifest_digest,
                precommitment.ledger_precommitment_digest == bound.ledger_digest,
                precommitment.runtime_policy_digest == policy.content_digest,
                precommitment.runtime_policy_digest == plan.config.base.runtime_policy_digest,
                precommitment.loaded_code_digest == _critical_loaded_code_digest_v4(policy),
                precommitment.external_gold_signed_report_digest
                == plan.config.external_gold_audit_signed_report_digest,
                precommitment.external_random_precommitment_record_sha256
                == bound.prepared.expected_random_selection_precommitment_record_sha256,
                precommitment.external_selection_precommit_anchor_evidence_digest
                == plan.config.external_selection_precommit_anchor_evidence_digest,
                precommitment.external_random_lane_capability_digest
                == plan.config.external_selection_capability_digest,
                precommitment.internal_author_precommitment_digest
                == plan.config.internal_selection_precommitment_digest,
                precommitment.internal_selection_digest
                == plan.config.internal_selection_capability_digest,
            )
        ):
            raise ValueError("scoring authority certificate differs from execution/plan roots")
        forbidden = {
            plan.config.base.scoring_authority_commitment,
            plan.config.base.audit_authority_commitment,
            plan.config.base.analysis_authority_commitment,
            precommitment.external_gold_report_authority_commitment,
        }
        if self.commitment in forbidden:
            raise ValueError("authenticated-score authority is not role-separated")
        self._verifier.verify(
            canonical_json(precommitment.unsigned_record()).encode("utf-8"),
            precommitment.authority_signature,
        )

    def replay_for_execution_authorization(
        self,
        bound: BoundRandomExternalExecutionV4,
        precommitment: RandomExternalScoringPrecommitmentV4,
        *,
        repository_root: str | Path,
        expected_signed_digest: str,
        external_gold_report: ExternalGoldAuditReportV4,
        external_gold_report_verifier: CanonicalEd25519VerifierV3,
        external_replay_inputs: ExternalGoldScoringReplayInputsV4,
        internal_replay_inputs: InternalGoldScoringReplayInputsV4,
    ) -> ScoringAuthorizationReplayV4:
        """Verify the signed certificate while every slot is still pre-STARTED."""

        self._verify_precommitment_roots(
            bound,
            precommitment,
            repository_root=repository_root,
            expected_signed_digest=expected_signed_digest,
        )
        ledger = bound.open_ledger()
        first = ledger.history_records()
        second = ledger.history_records()
        if (
            first != second
            or len(first) != RANDOM_EXTERNAL_PILOT_TASK_COUNT_V4
            or any(
                record.started is not None
                or record.selected_outcome is not None
                or record.terminal is not None
                for record in first
            )
        ):
            raise ValueError("scoring authorization replay must precede every STARTED record")
        plan = bound.prepared.plan
        external = _external_gold_replay_v4(
            plan,
            bound.prepared,
            external_gold_report,
            external_gold_report_verifier,
            external_replay_inputs,
        )
        internal = _internal_gold_replay_v4(plan, internal_replay_inputs)
        if not all(
            (
                external.report_authority_commitment
                == precommitment.external_gold_report_authority_commitment,
                external.signed_report_digest
                == precommitment.external_gold_signed_report_digest,
                external.random_replay_evidence_digest
                == precommitment.external_random_replay_evidence_digest,
                external.live_anchor_evidence_digest
                == precommitment.external_selection_precommit_anchor_evidence_digest,
                external.random_lane_capability_digest
                == precommitment.external_random_lane_capability_digest,
                external.random_gold_audit_input_digest
                == precommitment.external_random_gold_audit_input_digest,
                external.private_gold_vault_record_sha256
                == precommitment.external_private_gold_vault_record_sha256,
                external_replay_inputs.expected_precommitment_record_sha256
                == precommitment.external_random_precommitment_record_sha256,
                external_replay_inputs.expected_beacon_receipt_record_sha256
                == precommitment.external_random_beacon_receipt_record_sha256,
                internal.author_precommitment_digest
                == precommitment.internal_author_precommitment_digest,
                internal.selection_digest == precommitment.internal_selection_digest,
                internal.selection_private_record_digest
                == precommitment.internal_selection_private_record_digest,
                internal.audit_precommitment_digest
                == precommitment.internal_audit_precommitment_digest,
                internal.author_execution_manifest_digest
                == precommitment.internal_author_execution_manifest_digest,
            )
        ):
            raise ValueError("scoring authorization replay differs from exact gold authorities")
        replay = ScoringAuthorizationReplayV4(
            plan_digest=precommitment.plan_digest,
            authenticated_score_authority_commitment=self.commitment,
            scoring_precommitment_digest=precommitment.signed_digest,
            execution_precommitment_digest=precommitment.execution_precommitment_digest,
            execution_public_record_digest=precommitment.execution_public_record_digest,
            ledger_precommitment_digest=precommitment.ledger_precommitment_digest,
            runtime_policy_digest=precommitment.runtime_policy_digest,
            execution_implementation_manifest_digest=(
                precommitment.execution_implementation_manifest_digest
            ),
            loaded_code_digest=precommitment.loaded_code_digest,
        )
        replay.__post_init__()
        return replay

    def verify_acknowledged_precommitment(
        self,
        acknowledged: AcknowledgedRandomExternalExecutionV4,
        precommitment: RandomExternalScoringPrecommitmentV4,
        *,
        repository_root: str | Path,
        expected_signed_digest: str,
    ) -> None:
        """Verify post-run only when execution authorization transitively bound this digest."""

        if type(acknowledged) is not AcknowledgedRandomExternalExecutionV4:
            raise ValueError("post-run scoring requires exact acknowledged execution")
        acknowledged.__post_init__()
        acknowledged.verify_live()
        self._verify_precommitment_roots(
            acknowledged.bound,
            precommitment,
            repository_root=repository_root,
            expected_signed_digest=expected_signed_digest,
        )
        authorization_digest = getattr(
            acknowledged.authorization,
            "scoring_precommitment_digest",
            None,
        )
        if type(authorization_digest) is not str or not hmac.compare_digest(
            authorization_digest,
            precommitment.signed_digest,
        ):
            raise ValueError(
                "execution authorization did not bind the exact signed scoring precommitment"
            )

    def verify(
        self,
        plan: RandomExternalPilotPlanV4,
        score: AuthenticatedRandomExternalScoreV4,
    ) -> None:
        if type(self) is not AuthenticatedRandomScoreVerifierV4:
            raise ValueError("authenticated-score verifier must be exact")
        if type(plan) is not RandomExternalPilotPlanV4 or type(score) is not (
            AuthenticatedRandomExternalScoreV4
        ):
            raise ValueError("v4 scoring verification rejects legacy or nominal records")
        plan.__post_init__()
        assert_three_arm_protocol_config_v3(plan.config.base)
        score.__post_init__()
        if not hmac.compare_digest(
            self.commitment, plan.config.authenticated_score_authority_commitment
        ):
            raise ValueError("authenticated-score verifier differs from v4 precommitment")
        if self.commitment in {
            plan.config.base.scoring_authority_commitment,
            plan.config.base.audit_authority_commitment,
            plan.config.base.analysis_authority_commitment,
        }:
            raise ValueError("authenticated-score key aliases a legacy/audit/analysis authority")
        if score.plan_digest != plan.digest or score.plan_public_precommitment_digest != (
            plan.public_precommitment_digest
        ):
            raise ValueError("authenticated score belongs to another exact plan")
        assignment = plan.assignments[score.ordinal]
        case = next(item for item in plan.cases if item.case_id == assignment.case_id)
        if not all(
            (
                score.assignment_digest == assignment.digest,
                score.task_id == assignment.task_id,
                score.schedule_index == assignment.schedule_index,
                score.case_id == case.case_id,
                score.source is case.source,
                score.problem_digest == assignment.problem_digest == case.problem_digest,
                score.context_digest == assignment.context_digest,
                score.runtime_policy_digest == plan.config.base.runtime_policy_digest,
                score.scorer_spec_digest == case.scorer_spec_digest,
                score.scorer_manifest_digest == plan.config.base.scorer_manifest_digest,
            )
        ):
            raise ValueError("authenticated score differs from sealed assignment/config")
        self._verifier.verify(
            canonical_json(score.unsigned_record()).encode("utf-8"),
            score.authority_signature,
        )


@dataclass(frozen=True, slots=True)
class RandomExternalLedgerScoreEvidenceV4:
    """Exact acknowledged execution plus its execution-owned locked closure."""

    acknowledged: AcknowledgedRandomExternalExecutionV4 = field(repr=False)
    closure: RandomExternalExecutionClosureV4 = field(repr=False)
    expected_closure_digest: str
    repository_root: str
    ordinal: int

    def __post_init__(self) -> None:
        if (
            type(self) is not RandomExternalLedgerScoreEvidenceV4
            or type(self.acknowledged) is not AcknowledgedRandomExternalExecutionV4
            or type(self.closure) is not RandomExternalExecutionClosureV4
        ):
            raise ValueError("ledger-score evidence requires exact production records")
        self.acknowledged.__post_init__()
        self.closure.__post_init__()
        expected = _digest(self.expected_closure_digest, "expected execution closure")
        if not hmac.compare_digest(self.closure.content_digest, expected):
            raise ValueError("execution closure differs from external expectation")
        bound = self.acknowledged.bound
        if not all(
            (
                self.closure.execution_public_record_digest == bound.public_record_digest,
                self.closure.acknowledgement_evidence_digest
                == self.acknowledged.expected_ack_evidence_digest,
                self.closure.ledger_digest == bound.ledger_digest,
            )
        ):
            raise ValueError("execution closure belongs to another acknowledged ledger")
        resolved = _repository_root(self.repository_root)
        if str(resolved) != self.repository_root:
            raise ValueError("ledger-score repository root must be canonical")
        if type(self.ordinal) is not int or not (
            0 <= self.ordinal < RANDOM_EXTERNAL_PILOT_TASK_COUNT_V4
        ):
            raise ValueError("ledger-score evidence ordinal is invalid")


@dataclass(frozen=True, slots=True)
class _DerivedAttemptEvidenceV4:
    entry: RandomExternalClosedAttemptV4
    response_text: str = field(repr=False)
    response_present: bool
    terminal_evidence_digest: str
    terminal_record_present: bool
    transcript_digest: str
    response_digest: str
    model_evidence_digest: str
    executable_digest: str
    attempt_evidence_digest: str
    runtime_eligible: bool


def _derive_attempt_evidence_v4(
    evidence: RandomExternalLedgerScoreEvidenceV4,
) -> _DerivedAttemptEvidenceV4:
    evidence.__post_init__()
    acknowledged = evidence.acknowledged
    closure = evidence.closure
    bound = acknowledged.bound
    prepared = bound.prepared
    plan = prepared.plan
    task_map = prepared.task_map
    policy = prepared.policy
    assert_three_arm_protocol_config_v3(plan.config.base)

    # This is the authority boundary: the execution module locks the ledger, recovers
    # durable orphans, re-fetches both public GitHub anchors, validates the committed
    # Git/source manifest, cold-rebuilds all 72 attempts, and returns artifacts only
    # when the supplied closure is byte-for-byte identical to that fresh projection.
    artifacts = closed_artifacts_for_random_external_scoring_v4(
        acknowledged,
        closure,
        repository_root=evidence.repository_root,
    )
    if len(artifacts) != RANDOM_EXTERNAL_PILOT_TASK_COUNT_V4:
        raise ValueError("execution closure did not retain all 72 attempts")

    entry = closure.attempts[evidence.ordinal]
    slot = prepared.slots[evidence.ordinal]
    if (
        type(entry) is not RandomExternalClosedAttemptV4
        or type(slot) is not PreparedRandomExternalExecutionSlotV4
        or entry.ordinal != evidence.ordinal
        or entry.slot_id != slot.slot_id
        or entry.started.slot_id != slot.slot_id
        or entry.started.ordinal != evidence.ordinal
        or entry.started.ledger_digest != bound.ledger_digest
    ):
        raise ValueError("execution closure differs from exact prepared slot/STARTED record")
    started = entry.started
    if type(started) is not StartedAttempt or started.prerequisite_terminal_digest is not None:
        raise ValueError("v4 scoring requires one independent exact STARTED record")
    ledger = bound.open_ledger()
    ledger.verify_dispatch(slot.slot_id, slot.dispatch)
    projection = solver_projection_from_random_external_task_map_v4(
        task_map,
        plan,
        ordinal=evidence.ordinal,
    )
    prompt = render_solver_prompt_v3(projection)
    schema_raw = canonical_json(random_external_solver_output_schema_v4(projection)).encode(
        "utf-8"
    )
    if not all(
        (
            slot.private_task_id == plan.assignments[evidence.ordinal].task_id,
            slot.response_task_id == projection.task_id,
            slot.prompt_digest == solver_prompt_digest_v3(projection),
            slot.dispatch.prompt == prompt,
            slot.dispatch.output_schema_json == schema_raw,
            slot.output_schema_digest == _sha256(schema_raw),
            slot.dispatch.request_policy_digest == policy.content_digest,
            policy.content_digest == plan.config.base.runtime_policy_digest,
        )
    ):
        raise ValueError("ledger dispatch differs from cold-replayed v3 solver protocol")

    payload = artifacts[evidence.ordinal]
    closure_material = {
        "acknowledgement_evidence_digest": closure.acknowledgement_evidence_digest,
        "attempt": entry.commitment_record(),
        "attempt_universe_digest": closure.attempt_universe_digest,
        "execution_closure_digest": closure.content_digest,
        "ledger_precommitment_digest": bound.ledger_digest,
    }
    if entry.disposition is ClosedAttemptDispositionV4.BURNED_RUNNER_FAILURE:
        if payload is not None or entry.terminal is not None or entry.selected_outcome is not None:
            raise ValueError("burned runner failure cannot expose an outcome artifact")
        terminal_digest = _domain_digest(
            closure_material
            | {
                "burned_runner_failure_authenticator": (
                    entry.burned_runner_failure_authenticator
                )
            },
            domain="terminal-less-burned-attempt-evidence-v4",
        )
        transcript_digest = _domain_digest(
            closure_material,
            domain="unavailable-burned-transcript-evidence-v4",
        )
        model_digest = _domain_digest(
            closure_material | {"model_identity_verified": False},
            domain="unavailable-burned-model-evidence-v4",
        )
        response_text = ""
        response_present = False
        executable_digest = policy.executable_provenance.sha256_digest
        runtime_eligible = False
        terminal_present = False
    elif entry.disposition is ClosedAttemptDispositionV4.FAILED:
        if (
            type(entry.terminal) is not TerminalTransition
            or entry.terminal.kind is not OutcomeKind.FAILED
            or entry.selected_outcome is None
            or payload is None
            or payload.media_type != entry.selected_outcome.media_type
            or payload.media_type != "application/json"
        ):
            raise ValueError("failed closure lacks exact FAILED outcome evidence")
        failure_payload_digest = _sha256(payload.content)
        terminal_digest = entry.terminal.record_digest
        transcript_digest = _domain_digest(
            closure_material | {"failure_payload_digest": failure_payload_digest},
            domain="failed-attempt-transcript-evidence-v4",
        )
        model_digest = _domain_digest(
            closure_material | {"model_identity_verified": False},
            domain="failed-attempt-model-evidence-v4",
        )
        response_text = ""
        response_present = False
        executable_digest = policy.executable_provenance.sha256_digest
        runtime_eligible = False
        terminal_present = True
    else:
        if (
            entry.disposition is not ClosedAttemptDispositionV4.RETURNED
            or type(entry.terminal) is not TerminalTransition
            or entry.terminal.kind is not OutcomeKind.RETURNED
            or entry.selected_outcome is None
            or payload is None
            or payload.media_type != entry.selected_outcome.media_type
            or payload.media_type != RANDOM_EXTERNAL_SOLVER_ARTIFACT_MEDIA_TYPE_V4
        ):
            raise ValueError("returned closure lacks exact RETURNED outcome evidence")
        try:
            artifact = deserialize_codex_run_result(payload.content)
        except (CodexArtifactError, TypeError, ValueError) as error:
            raise ValueError("returned ledger artifact fails exact Codex replay") from error
        if type(artifact) is not CodexRunResult:
            raise ValueError("returned ledger artifact is not an exact CodexRunResult")
        artifact.__post_init__()
        expected_executable = policy.executable_provenance
        if not all(
            (
                artifact.prompt_text == prompt,
                artifact.output_schema_json == schema_raw,
                artifact.requested_model == policy.model,
                artifact.requested_effort == policy.effort,
                artifact.requested_provider == policy.model_provider,
                artifact.timeout_seconds == policy.timeout_seconds,
                artifact.executable_path == expected_executable.canonical_path,
                artifact.executable_size_bytes == expected_executable.size_bytes,
                artifact.executable_sha256_digest == expected_executable.sha256_digest,
                artifact.solver_process_isolation_policy_digest
                == policy.solver_isolation_policy_digest,
                artifact.runtime_directory_device == started.runtime_device,
                artifact.runtime_directory_inode == started.runtime_inode,
                artifact.standard_popen_used is True,
                artifact.executable_lock_enforced is True,
                artifact.audit.model_identity_verified is False,
            )
        ):
            raise ValueError("returned Codex artifact differs from exact sealed execution")
        response_present = type(artifact.output_text) is str
        response_text = artifact.output_text if response_present else ""
        transcript_digest = artifact.transcript_hash
        executable_digest = artifact.executable_sha256_digest
        runtime_eligible = bool(
            response_present
            and artifact.audit.eligible
            and artifact.audit.accepted_model == policy.model
            and artifact.audit.accepted_provider == policy.model_provider
            and artifact.audit.accepted_effort == policy.effort
            and artifact.audit.reroutes == ()
            and artifact.audit.final_message_count == 1
            and artifact.codex_home_initial_auth_only
            and artifact.codex_home_cleanup_verified
            and artifact.credential_leak_scan_passed
        )
        model_digest = _domain_digest(
            {
                "accepted_effort": artifact.audit.accepted_effort,
                "accepted_model": artifact.audit.accepted_model,
                "accepted_provider": artifact.audit.accepted_provider,
                "audit_eligible": artifact.audit.eligible,
                "audit_reasons": list(artifact.audit.reasons),
                "model_identity_verified": False,
                "requested_effort": artifact.requested_effort,
                "requested_model": artifact.requested_model,
                "requested_provider": artifact.requested_provider,
                "transcript_digest": artifact.transcript_hash,
            },
            domain="codex-model-observation-evidence-v4",
        )
        terminal_digest = entry.terminal.record_digest
        terminal_present = True
    response_digest = _sha256(response_text.encode("utf-8"))
    attempt_digest = _domain_digest(
        closure_material
        | {
            "executable_digest": executable_digest,
            "model_evidence_digest": model_digest,
            "prompt_digest": slot.prompt_digest,
            "response_digest": response_digest,
            "response_present": response_present,
            "runtime_eligible": runtime_eligible,
            "terminal_evidence_digest": terminal_digest,
            "terminal_record_present": terminal_present,
            "transcript_digest": transcript_digest,
        },
        domain="derived-ledger-attempt-evidence-v4",
    )
    return _DerivedAttemptEvidenceV4(
        entry=entry,
        response_text=response_text,
        response_present=response_present,
        terminal_evidence_digest=terminal_digest,
        terminal_record_present=terminal_present,
        transcript_digest=transcript_digest,
        response_digest=response_digest,
        model_evidence_digest=model_digest,
        executable_digest=executable_digest,
        attempt_evidence_digest=attempt_digest,
        runtime_eligible=runtime_eligible,
    )


def _outcome_from_score_result_v4(result: ScoreResultEvidenceV3) -> AttemptOutcomeV4:
    if type(result) is not ScoreResultEvidenceV3:
        raise ValueError("outcome derivation requires exact deterministic score evidence")
    result.__post_init__()
    if result.disposition is ScoreDispositionV3.SCORED:
        return AttemptOutcomeV4.CORRECT if result.correct else AttemptOutcomeV4.INCORRECT
    if result.disposition is ScoreDispositionV3.ABSTAINED:
        return AttemptOutcomeV4.SOLVER_ABSTENTION
    return AttemptOutcomeV4.INVALID_OUTPUT


class AuthenticatedRandomExternalScorerV4:
    """Exact-seed authority that alone can mint authenticated v4 scores."""

    __slots__ = ("_signer",)

    def __init__(self, private_seed: bytes) -> None:
        if type(self) is not AuthenticatedRandomExternalScorerV4:
            raise ValueError("authenticated scorer subclasses are forbidden")
        self._signer = CanonicalEd25519SignerV3(
            private_seed,
            domain=RANDOM_EXTERNAL_AUTHENTICATED_SCORE_SIGNING_DOMAIN_V4,
        )

    @property
    def commitment(self) -> str:
        return self._signer.authority_commitment

    def verifier(self) -> AuthenticatedRandomScoreVerifierV4:
        return AuthenticatedRandomScoreVerifierV4(
            self._signer.verifier().public_key_bytes()
        )

    def seal_precommitment(
        self,
        bound: BoundRandomExternalExecutionV4,
        *,
        repository_root: str | Path,
        external_gold_report: ExternalGoldAuditReportV4,
        external_gold_report_verifier: CanonicalEd25519VerifierV3,
        external_replay_inputs: ExternalGoldScoringReplayInputsV4,
        internal_replay_inputs: InternalGoldScoringReplayInputsV4,
    ) -> RandomExternalScoringPrecommitmentV4:
        """Mint only while every exact ledger slot is still PENDING."""

        if type(bound) is not BoundRandomExternalExecutionV4:
            raise ValueError("scoring precommitment requires exact pending bound execution")
        bound.__post_init__()
        prepared = bound.prepared
        prepared.policy.validate_current(repository_root=_repository_root(repository_root))
        _verify_execution_live_anchor_v4(prepared)
        plan = prepared.plan
        assert_three_arm_protocol_config_v3(plan.config.base)
        if not hmac.compare_digest(
            self.commitment, plan.config.authenticated_score_authority_commitment
        ):
            raise ValueError("authenticated scorer differs from the distinct v4 plan authority")
        ledger = bound.open_ledger()
        first = ledger.history_records()
        second = ledger.history_records()
        if first != second or any(
            record.started is not None
            or record.selected_outcome is not None
            or record.terminal is not None
            for record in first
        ):
            raise ValueError("scoring authority certificate must precede every STARTED record")
        external = _external_gold_replay_v4(
            plan,
            prepared,
            external_gold_report,
            external_gold_report_verifier,
            external_replay_inputs,
        )
        internal = _internal_gold_replay_v4(plan, internal_replay_inputs)
        forbidden = {
            plan.config.base.scoring_authority_commitment,
            plan.config.base.audit_authority_commitment,
            plan.config.base.analysis_authority_commitment,
            external.report_authority_commitment,
        }
        if self.commitment in forbidden:
            raise ValueError("authenticated-score authority aliases another custody role")
        pending = "ed25519:" + "0" * 128
        draft = RandomExternalScoringPrecommitmentV4(
            plan_digest=plan.digest,
            plan_public_precommitment_digest=plan.public_precommitment_digest,
            task_map_public_precommitment_digest=prepared.task_map.public_precommitment_digest,
            authenticated_score_authority_commitment=self.commitment,
            execution_precommitment_digest=prepared.public_precommitment_digest,
            execution_public_record_digest=bound.public_record_digest,
            execution_implementation_manifest_digest=(
                prepared.policy.implementation_manifest_digest
            ),
            ledger_precommitment_digest=bound.ledger_digest,
            runtime_policy_digest=prepared.policy.content_digest,
            loaded_code_digest=_critical_loaded_code_digest_v4(prepared.policy),
            external_gold_report_authority_commitment=external.report_authority_commitment,
            external_gold_signed_report_digest=external.signed_report_digest,
            external_random_replay_evidence_digest=external.random_replay_evidence_digest,
            external_random_precommitment_record_sha256=(
                external_replay_inputs.expected_precommitment_record_sha256
            ),
            external_random_beacon_receipt_record_sha256=(
                external_replay_inputs.expected_beacon_receipt_record_sha256
            ),
            external_selection_precommit_anchor_evidence_digest=(
                external.live_anchor_evidence_digest
            ),
            external_random_lane_capability_digest=external.random_lane_capability_digest,
            external_random_gold_audit_input_digest=external.random_gold_audit_input_digest,
            external_private_gold_vault_record_sha256=(
                external.private_gold_vault_record_sha256
            ),
            internal_author_precommitment_digest=internal.author_precommitment_digest,
            internal_selection_digest=internal.selection_digest,
            internal_selection_private_record_digest=(
                internal.selection_private_record_digest
            ),
            internal_audit_precommitment_digest=internal.audit_precommitment_digest,
            internal_author_execution_manifest_digest=(
                internal.author_execution_manifest_digest
            ),
            authority_signature=pending,
        )
        signed = replace(
            draft,
            authority_signature=self._signer.sign(
                canonical_json(draft.unsigned_record()).encode("utf-8")
            ),
        )
        self.verifier().replay_for_execution_authorization(
            bound,
            signed,
            repository_root=repository_root,
            expected_signed_digest=signed.signed_digest,
            external_gold_report=external_gold_report,
            external_gold_report_verifier=external_gold_report_verifier,
            external_replay_inputs=external_replay_inputs,
            internal_replay_inputs=internal_replay_inputs,
        )
        return signed

    def score(
        self,
        precommitment: RandomExternalScoringPrecommitmentV4,
        ledger_evidence: RandomExternalLedgerScoreEvidenceV4,
        *,
        expected_scoring_precommitment_digest: str,
        external_gold_report: ExternalGoldAuditReportV4 | None = None,
        external_gold_report_verifier: CanonicalEd25519VerifierV3 | None = None,
        external_replay_inputs: ExternalGoldScoringReplayInputsV4 | None = None,
        internal_replay_inputs: InternalGoldScoringReplayInputsV4 | None = None,
    ) -> AuthenticatedRandomExternalScoreV4:
        """Derive one ledger outcome and sign it; there is no outcome/flag input."""

        if (
            type(precommitment) is not RandomExternalScoringPrecommitmentV4
            or type(ledger_evidence) is not RandomExternalLedgerScoreEvidenceV4
        ):
            raise ValueError("authenticated scoring requires exact certificate/ledger evidence")
        acknowledged = ledger_evidence.acknowledged
        bound = acknowledged.bound
        verifier = self.verifier()
        verifier.verify_acknowledged_precommitment(
            acknowledged,
            precommitment,
            repository_root=ledger_evidence.repository_root,
            expected_signed_digest=expected_scoring_precommitment_digest,
        )
        if not hmac.compare_digest(self.commitment, verifier.commitment):
            raise ValueError("authenticated scorer/verifier key mismatch")
        derived = _derive_attempt_evidence_v4(ledger_evidence)
        prepared = bound.prepared
        plan = prepared.plan
        task_map = prepared.task_map
        assignment = plan.assignments[ledger_evidence.ordinal]
        case = next(item for item in plan.cases if item.case_id == assignment.case_id)
        binding = task_map.binding_at(ledger_evidence.ordinal)
        slot = prepared.slots[ledger_evidence.ordinal]
        if not all(
            (
                binding.schedule_index == assignment.schedule_index,
                binding.private_task_id == assignment.task_id,
                binding.response_task_id == slot.response_task_id,
                _SCORE_ID_RE.fullmatch(binding.score_id) is not None,
                binding.problem_digest == assignment.problem_digest,
                binding.context_digest == assignment.context_digest,
            )
        ):
            raise ValueError("scoring task binding differs from exact sealed map")

        answer: ExactRationalV3
        gold_authority_digest: str
        upstream_gold_commitment: str
        if case.source is ProblemSource.EXTERNAL:
            if (
                type(external_gold_report) is not ExternalGoldAuditReportV4
                or type(external_gold_report_verifier) is not CanonicalEd25519VerifierV3
                or type(external_replay_inputs) is not ExternalGoldScoringReplayInputsV4
            ):
                raise ValueError("external score requires the complete real signed-report replay")
            external = _external_gold_replay_v4(
                plan,
                prepared,
                external_gold_report,
                external_gold_report_verifier,
                external_replay_inputs,
            )
            if not all(
                (
                    external.report_authority_commitment
                    == precommitment.external_gold_report_authority_commitment,
                    external.signed_report_digest
                    == precommitment.external_gold_signed_report_digest,
                    external.random_replay_evidence_digest
                    == precommitment.external_random_replay_evidence_digest,
                    external.random_lane_capability_digest
                    == precommitment.external_random_lane_capability_digest,
                    external.random_gold_audit_input_digest
                    == precommitment.external_random_gold_audit_input_digest,
                    external.private_gold_vault_record_sha256
                    == precommitment.external_private_gold_vault_record_sha256,
                    external.live_anchor_evidence_digest
                    == precommitment.external_selection_precommit_anchor_evidence_digest,
                )
            ):
                raise ValueError("external gold replay differs from scoring certificate")
            answer = external.answers[case.case_id]
            upstream_gold_commitment = external.case_commitments[case.case_id]
            gold_authority_digest = external.signed_report_digest
        else:
            if type(internal_replay_inputs) is not InternalGoldScoringReplayInputsV4:
                raise ValueError("internal score requires the complete hardened selector replay")
            internal = _internal_gold_replay_v4(plan, internal_replay_inputs)
            if not all(
                (
                    internal.author_precommitment_digest
                    == precommitment.internal_author_precommitment_digest,
                    internal.selection_digest == precommitment.internal_selection_digest,
                    internal.selection_private_record_digest
                    == precommitment.internal_selection_private_record_digest,
                    internal.audit_precommitment_digest
                    == precommitment.internal_audit_precommitment_digest,
                    internal.author_execution_manifest_digest
                    == precommitment.internal_author_execution_manifest_digest,
                )
            ):
                raise ValueError("internal gold replay differs from scoring certificate")
            answer = internal.answers[case.case_id]
            upstream_gold_commitment = internal.case_commitments[case.case_id]
            gold_authority_digest = internal.selection_private_record_digest
        gold_case_binding_digest = _domain_digest(
            {
                "case_id": case.case_id,
                "gold_authority_digest": gold_authority_digest,
                "problem_digest": case.problem_digest,
                "source": case.source.value,
                "upstream_gold_commitment": upstream_gold_commitment,
            },
            domain="authenticated-gold-case-binding-v4",
        )

        score_result: ScoreResultEvidenceV3 | None = None
        if (
            derived.entry.disposition is not ClosedAttemptDispositionV4.RETURNED
            or not derived.response_present
        ):
            outcome = AttemptOutcomeV4.RUNNER_FAILURE
        else:
            capsule = ArmBlindScoreCapsuleV3(
                score_id=binding.score_id,
                task_id=binding.response_task_id,
                problem_digest=case.problem_digest,
                response_text=derived.response_text,
                expected_answer=answer,
            )
            score_result = score_arm_blind_capsule_v3(capsule)
            outcome = _outcome_from_score_result_v4(score_result)
        scorer_evidence_digest = _domain_digest(
            {
                "attempt_evidence_digest": derived.attempt_evidence_digest,
                "gold_case_binding_digest": gold_case_binding_digest,
                "loaded_code_digest": precommitment.loaded_code_digest,
                "parser_digest": precommitment.parser_digest,
                "score_result_digest": (
                    None if score_result is None else score_result.digest
                ),
                "scorer_manifest_digest": precommitment.scorer_manifest_digest,
                "scoring_rule_digest": precommitment.scoring_rule_digest,
            },
            domain="deterministic-scorer-evidence-v4",
        )
        pending = "ed25519:" + "0" * 128
        draft = AuthenticatedRandomExternalScoreV4(
            plan_digest=plan.digest,
            plan_public_precommitment_digest=plan.public_precommitment_digest,
            task_map_public_precommitment_digest=task_map.public_precommitment_digest,
            scoring_precommitment_digest=precommitment.signed_digest,
            execution_public_record_digest=bound.public_record_digest,
            ledger_precommitment_digest=bound.ledger_digest,
            closed_attempt_manifest_digest=ledger_evidence.closure.content_digest,
            ordinal=ledger_evidence.ordinal,
            schedule_index=assignment.schedule_index,
            assignment_digest=assignment.digest,
            case_id=case.case_id,
            source=case.source,
            task_id=assignment.task_id,
            response_task_id=binding.response_task_id,
            score_id=binding.score_id,
            problem_digest=assignment.problem_digest,
            context_digest=assignment.context_digest,
            prompt_digest=slot.prompt_digest,
            started_record_digest=derived.entry.started.record_digest,
            terminal_record_digest=derived.terminal_evidence_digest,
            terminal_record_present=derived.terminal_record_present,
            closed_attempt_disposition=derived.entry.disposition,
            ledger_manifest_digest=ledger_evidence.closure.content_digest,
            attempt_evidence_digest=derived.attempt_evidence_digest,
            transcript_digest=derived.transcript_digest,
            response_digest=derived.response_digest,
            model_evidence_digest=derived.model_evidence_digest,
            executable_digest=derived.executable_digest,
            loaded_code_digest=precommitment.loaded_code_digest,
            parser_digest=precommitment.parser_digest,
            scorer_spec_digest=case.scorer_spec_digest,
            scorer_manifest_digest=precommitment.scorer_manifest_digest,
            scorer_evidence_digest=scorer_evidence_digest,
            runtime_policy_digest=prepared.policy.content_digest,
            gold_authority_digest=gold_authority_digest,
            gold_case_binding_digest=gold_case_binding_digest,
            scoring_rule_digest=precommitment.scoring_rule_digest,
            score_result_digest=None if score_result is None else score_result.digest,
            parsed_output_digest=(
                None if score_result is None else score_result.parsed_output_digest
            ),
            submitted_answer=None if score_result is None else score_result.submitted_answer,
            parser_disposition=None if score_result is None else score_result.disposition,
            outcome=outcome,
            runtime_eligible=(derived.runtime_eligible and score_result is not None),
            started_once_verified=True,
            served_model=prepared.policy.model,
            effort=prepared.policy.effort,
            model_identity_verified=False,
            scorer_blinded=True,
            authority_signature=pending,
        )
        signed = replace(
            draft,
            authority_signature=self._signer.sign(
                canonical_json(draft.unsigned_record()).encode("utf-8")
            ),
        )
        verifier.verify(plan, signed)
        return signed


def replay_random_external_scoring_precommitment_for_execution_v4(
    verifier: AuthenticatedRandomScoreVerifierV4,
    bound: BoundRandomExternalExecutionV4,
    precommitment: RandomExternalScoringPrecommitmentV4,
    *,
    repository_root: str | Path,
    expected_signed_digest: str,
    external_gold_report: ExternalGoldAuditReportV4,
    external_gold_report_verifier: CanonicalEd25519VerifierV3,
    external_replay_inputs: ExternalGoldScoringReplayInputsV4,
    internal_replay_inputs: InternalGoldScoringReplayInputsV4,
) -> ScoringAuthorizationReplayV4:
    """Exact raw-input adapter for the production execution-authorization builder."""

    if type(verifier) is not AuthenticatedRandomScoreVerifierV4:
        raise ValueError("execution authorization requires exact scoring verifier")
    return verifier.replay_for_execution_authorization(
        bound,
        precommitment,
        repository_root=repository_root,
        expected_signed_digest=expected_signed_digest,
        external_gold_report=external_gold_report,
        external_gold_report_verifier=external_gold_report_verifier,
        external_replay_inputs=external_replay_inputs,
        internal_replay_inputs=internal_replay_inputs,
    )


def score_authenticated_random_external_attempt_v4(
    scorer: AuthenticatedRandomExternalScorerV4,
    precommitment: RandomExternalScoringPrecommitmentV4,
    ledger_evidence: RandomExternalLedgerScoreEvidenceV4,
    *,
    expected_scoring_precommitment_digest: str,
    external_gold_report: ExternalGoldAuditReportV4 | None = None,
    external_gold_report_verifier: CanonicalEd25519VerifierV3 | None = None,
    external_replay_inputs: ExternalGoldScoringReplayInputsV4 | None = None,
    internal_replay_inputs: InternalGoldScoringReplayInputsV4 | None = None,
) -> AuthenticatedRandomExternalScoreV4:
    if type(scorer) is not AuthenticatedRandomExternalScorerV4:
        raise ValueError("authenticated scoring requires exact scorer authority")
    return scorer.score(
        precommitment,
        ledger_evidence,
        expected_scoring_precommitment_digest=expected_scoring_precommitment_digest,
        external_gold_report=external_gold_report,
        external_gold_report_verifier=external_gold_report_verifier,
        external_replay_inputs=external_replay_inputs,
        internal_replay_inputs=internal_replay_inputs,
    )


__all__ = [
    "RANDOM_EXTERNAL_AUTHENTICATED_SCORE_SCHEMA_V4",
    "RANDOM_EXTERNAL_AUTHENTICATED_SCORE_SIGNING_DOMAIN_V4",
    "RANDOM_EXTERNAL_SCORING_AUTHORIZATION_REPLAY_SCHEMA_V4",
    "RANDOM_EXTERNAL_SCORING_PRECOMMITMENT_SCHEMA_V4",
    "AttemptOutcomeV4",
    "AuthenticatedRandomExternalScoreV4",
    "AuthenticatedRandomExternalScorerV4",
    "AuthenticatedRandomScoreVerifierV4",
    "ClosedAttemptDispositionV4",
    "ExternalGoldScoringReplayInputsV4",
    "InternalGoldScoringReplayInputsV4",
    "RandomExternalLedgerScoreEvidenceV4",
    "RandomExternalScoringPrecommitmentV4",
    "ScoringAuthorizationReplayV4",
    "replay_random_external_scoring_precommitment_for_execution_v4",
    "score_authenticated_random_external_attempt_v4",
]
