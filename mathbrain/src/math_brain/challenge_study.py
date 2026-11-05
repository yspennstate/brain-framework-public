"""Pre-register and summarize the sealed paired challenge evaluation.

This module deliberately separates preparation from execution.  Holdout cases are
opened only after a revision, executable image, runtime policy, prompt, seeds, and
no-improvement-use acknowledgement have been committed.  The returned ledger slots
contain HMAC commitments, while raw holdout views remain private runtime material.
"""

from __future__ import annotations

import hashlib
import json
import re
import secrets
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from .attempt_ledger import (
    ArtifactPayload,
    AttemptLedger,
    DispatchMaterial,
    OutcomeKind,
    SlotCommitment,
    SlotState,
    StartedAttempt,
    TerminalTransition,
    new_slot_id,
)
from .challenge_bank import (
    HoldoutUnsealRequest,
    ReferenceCorpus,
    RetrievalAuditRecord,
    SealedChallengeBank,
    content_digest,
    load_challenge_bank,
)
from .codex_runner import (
    CodexAppServerRunner,
    CodexRunConfig,
    ExecutableProvenance,
    capture_codex_executable_provenance,
)
from .evaluation import (
    AttemptStatus,
    Condition,
    DesignSeeds,
    EvaluationCase,
    EvaluationConfig,
    EvaluationPlan,
    EvaluationRun,
    aggregate,
    build_full_plan,
    codex_output_schema_json,
    codex_prompt_for_batch,
    decide_benefit,
    failed_batch,
    finalize_run,
    pair_deltas,
    parse_codex_artifact_response,
    retrieval_evidence_for_plan,
)


STUDY_SCHEMA = "org.math-research-brain/sealed-challenge-study"
STUDY_SCHEMA_VERSION = 1
PRIVATE_RUN_MAX_TRANSCRIPT_BYTES = 8_000_000

SOLVER_PROMPT = """Independently audit every ordered mathematical case using only that case's solver_view and brain_context. The context is generic fallible guidance, never evidence. Return exactly the supplied JSON schema and preserve the supplied case order, case_id, and view_digest.

For every case, report probability_invalid in [0,1]. If the derivation is invalid, report the first invalid numbered line, choose the most precise canonical error_family, provide a replacement_line that includes the original line number and `claim = ` prefix, and provide a witness when possible. For a structured witness, set witness to {\"typed_payload\": VALUE}, where VALUE is the corrected JSON value after `claim = `; for a direct unequal-value witness, use {\"left_value\": \"...\", \"right_value\": \"...\"}. For a valid derivation, use null for first_invalid_line, error_family, repair, and witness.

Canonical error families are: quantifier_scope, illegal_cancellation, branch_loss, induction_reachability, index_region, norm_spectral_radius, local_global, feasible_boundary, asymptotic_scale, implication_equivalence, modular_cancellation, candidate_validation, overlap_omission, missing_coprimality, linear_invariant, independence_assumption, sign_case, initial_data, aggregate_pointwise, degree_threshold. Do not mention experimental conditions or hidden answer keys."""


def _sha256_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


@dataclass(frozen=True, slots=True)
class StudySecrets:
    """Caller-held material that never enters a solver environment."""

    seeds: DesignSeeds
    runtime_salt: bytes
    ledger_key: bytes

    def __post_init__(self) -> None:
        if not isinstance(self.seeds, DesignSeeds):
            raise ValueError("study secrets require exact DesignSeeds")
        if not isinstance(self.runtime_salt, bytes) or len(self.runtime_salt) < 32:
            raise ValueError("runtime salt must contain at least 256 bits")
        if not isinstance(self.ledger_key, bytes) or len(self.ledger_key) < 32:
            raise ValueError("ledger key must contain at least 256 bits")

    @classmethod
    def generate(cls) -> StudySecrets:
        return cls(
            seeds=DesignSeeds(
                order=secrets.token_hex(32),
                variant=secrets.token_hex(32),
                pair=secrets.token_hex(32),
                condition=secrets.token_hex(32),
                bootstrap=secrets.token_hex(32),
            ),
            runtime_salt=secrets.token_bytes(32),
            ledger_key=secrets.token_bytes(32),
        )


@dataclass(frozen=True, slots=True)
class StudyPreregistration:
    """Public-only study choices frozen before the private payload is read."""

    bank: SealedChallengeBank
    config: EvaluationConfig
    evaluator_manifest_digest: str
    brain_revision: str
    executable_provenance: ExecutableProvenance

    def __post_init__(self) -> None:
        if type(self.bank) is not SealedChallengeBank:
            raise ValueError("study preregistration requires an exact sealed bank")
        if type(self.config) is not EvaluationConfig:
            raise ValueError("study preregistration requires an exact evaluation config")
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", self.evaluator_manifest_digest):
            raise ValueError("evaluator manifest digest must be canonical SHA-256")
        if not re.fullmatch(r"[0-9a-f]{40,64}", self.brain_revision):
            raise ValueError("brain revision must be a lowercase Git object id")
        if type(self.executable_provenance) is not ExecutableProvenance:
            raise ValueError("study preregistration requires exact executable provenance")
        if self.config.runtime.executable_provenance != self.executable_provenance:
            raise ValueError("preregistered config and executable provenance differ")

    def manifest_material(self, secrets_material: StudySecrets) -> dict[str, Any]:
        """Reconstruct and verify the exact public pre-unseal commitment material."""

        if not isinstance(secrets_material, StudySecrets):
            raise ValueError("preregistration verification requires StudySecrets")
        material = _manifest_material(
            bank=self.bank,
            config=self.config,
            seeds=secrets_material.seeds,
            runtime_salt=secrets_material.runtime_salt,
            brain_revision=self.brain_revision,
        )
        if content_digest(material) != self.evaluator_manifest_digest:
            raise ValueError("study secrets differ from the preregistered manifest")
        return material

    def private_preregistration_record(
        self, secrets_material: StudySecrets
    ) -> dict[str, Any]:
        """Return fsync-ready material containing commitments but no raw secrets."""

        return {
            "evaluator_manifest_digest": self.evaluator_manifest_digest,
            "manifest_material": self.manifest_material(secrets_material),
            "phase": "before_holdout_payload_read",
            "schema": STUDY_SCHEMA,
            "schema_version": STUDY_SCHEMA_VERSION,
        }


@dataclass(frozen=True, slots=True)
class PreparedSlot:
    ordinal: int
    slot_id: str
    pair_id: int
    condition: Condition
    dispatch: DispatchMaterial
    commitment: SlotCommitment

    def __post_init__(self) -> None:
        if type(self.ordinal) is not int or self.ordinal < 0:
            raise ValueError("slot ordinal must be a nonnegative exact integer")
        if type(self.pair_id) is not int or self.pair_id < 0:
            raise ValueError("slot pair id must be a nonnegative exact integer")
        if not isinstance(self.condition, Condition):
            raise ValueError("slot condition must be declared")
        if not isinstance(self.dispatch, DispatchMaterial):
            raise ValueError("slot requires exact dispatch material")
        if not isinstance(self.commitment, SlotCommitment):
            raise ValueError("slot requires an exact ledger commitment")
        if self.commitment.slot_id != self.slot_id:
            raise ValueError("slot id differs from its commitment")


@dataclass(frozen=True, slots=True)
class PreparedChallengeStudy:
    plan: EvaluationPlan
    evaluator_manifest_digest: str
    brain_revision: str
    executable_provenance: ExecutableProvenance
    slots: tuple[PreparedSlot, ...]
    retrieval_audits: tuple[tuple[str, RetrievalAuditRecord], ...]

    def __post_init__(self) -> None:
        if not isinstance(self.plan, EvaluationPlan):
            raise ValueError("prepared study requires a sealed EvaluationPlan")
        if not re.fullmatch(r"[0-9a-f]{40,64}", self.brain_revision):
            raise ValueError("brain revision must be a lowercase Git object id")
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", self.evaluator_manifest_digest):
            raise ValueError("evaluator manifest digest must be canonical SHA-256")
        if type(self.executable_provenance) is not ExecutableProvenance:
            raise ValueError("prepared study requires exact executable provenance")
        expected_slots = self.plan.config.pair_count * len(Condition)
        if len(self.slots) != expected_slots:
            raise ValueError("prepared study has an incomplete slot universe")
        if tuple(slot.ordinal for slot in self.slots) != tuple(range(expected_slots)):
            raise ValueError("prepared slots must retain exact dispatch order")
        if len({slot.slot_id for slot in self.slots}) != expected_slots:
            raise ValueError("prepared slot ids must be unique")
        if self.plan.config.runtime.executable_provenance != self.executable_provenance:
            raise ValueError("plan and study executable provenance differ")
        expected_dispatches = []
        for pair_dispatch in sorted(
            self.plan.pair_dispatch, key=lambda item: item.dispatch_index
        ):
            for condition in pair_dispatch.condition_order:
                batch = self.plan.batch(pair_dispatch.pair_id, condition)
                expected_dispatches.append(
                    (
                        pair_dispatch.pair_id,
                        condition,
                        DispatchMaterial(
                            run_spec_digest=self.plan.spec.digest,
                            batch_digest=batch.digest,
                            prompt=codex_prompt_for_batch(batch),
                            output_schema_json=codex_output_schema_json(batch),
                            request_policy_digest=self.plan.spec.runtime_policy_digest,
                            dispatch_nonce=batch.dispatch_nonce,
                        ),
                    )
                )
        for slot, (pair_id, condition, dispatch) in zip(
            self.slots, expected_dispatches, strict=True
        ):
            if (
                slot.pair_id != pair_id
                or slot.condition is not condition
                or slot.dispatch != dispatch
            ):
                raise ValueError("prepared slot differs from the sealed dispatch plan")
        if tuple(case_id for case_id, _audit in self.retrieval_audits) != (
            self.plan.spec.design_case_ids
        ):
            raise ValueError("retrieval audits differ from the sealed case universe")

    @property
    def commitments(self) -> tuple[SlotCommitment, ...]:
        return tuple(slot.commitment for slot in self.slots)

    def private_precommitment_record(self) -> dict[str, Any]:
        """Return digests and the evaluator-only unblinding map, never case content."""

        return {
            "brain_revision": self.brain_revision,
            "evaluator_manifest_digest": self.evaluator_manifest_digest,
            "executable_provenance": self.executable_provenance.to_dict(),
            "retrieval_audits": [
                {"case_id": case_id, "record": audit.to_dict()}
                for case_id, audit in self.retrieval_audits
            ],
            "run_spec": self.plan.spec.to_dict(),
            "schema": STUDY_SCHEMA,
            "schema_version": STUDY_SCHEMA_VERSION,
            "slots": [
                {
                    "commitment": slot.commitment.to_dict(ordinal=slot.ordinal),
                    "condition": slot.condition.value,
                    "ordinal": slot.ordinal,
                    "pair_id": slot.pair_id,
                    "slot_id": slot.slot_id,
                }
                for slot in self.slots
            ],
        }


_EVIDENCE_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")
_BUILT_BY_EXECUTION = object()
"""Set only by execute_prepared_study, after it has re-read every terminal record and selected outcome."""


@dataclass(frozen=True, slots=True)
class StudyExecution:
    """Fully persisted and only then unblinded/scored study evidence.

    Constructing one checks its shape: canonical digests, unique slot ids, a complete terminal universe, and matching
    terminal and outcome slots. Whether its digests are the ledger's is a separate fact. The summary says the ledger is
    verified only when execute_prepared_study built the object (it reads every record back before building it) or when
    verify_against_ledger has just rechecked it against the ledger. Until 29 Sep the summary said verified for any
    object the constructor accepted, and the constructor accepted repeated slot ids and non-digest strings (scheduled
    audit account03/B09, B09-R79-STUDYEXECUTION-VERIFIED-SUMMARY-UNBOUND-20260929).
    """

    run: EvaluationRun
    ledger_digest: str
    terminal_record_digests: tuple[tuple[str, str], ...]
    outcome_object_digests: tuple[tuple[str, str], ...]
    built_by: object = field(default=None, compare=False, repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.run, EvaluationRun):
            raise ValueError("study execution requires an exact EvaluationRun")
        if not isinstance(self.ledger_digest, str) or not _EVIDENCE_DIGEST.fullmatch(self.ledger_digest):
            raise ValueError("study execution ledger digest is invalid")
        if len(self.terminal_record_digests) != len(self.run.plan.spec.required_pair_ids) * 2:
            raise ValueError("study execution terminal universe is incomplete")
        if tuple(slot for slot, _digest in self.terminal_record_digests) != tuple(
            slot for slot, _digest in self.outcome_object_digests
        ):
            raise ValueError("terminal and outcome slot universes differ")
        slots = [slot for slot, _digest in self.terminal_record_digests]
        if len(set(slots)) != len(slots) or not all(isinstance(slot, str) and slot for slot in slots):
            raise ValueError("study execution slot ids must be unique nonempty text")
        for _slot, digest in self.terminal_record_digests + self.outcome_object_digests:
            if not isinstance(digest, str) or not _EVIDENCE_DIGEST.fullmatch(digest):
                raise ValueError("study execution record digests must be sha256: followed by 64 lowercase hex digits")

    def verify_against_ledger(self, ledger: AttemptLedger) -> bool:
        """Recheck this evidence against the durable ledger, reading only: the same ledger, the same ordered slot
        universe, and for every slot a terminal state whose terminal record and selected outcome carry exactly these
        digests."""

        if not isinstance(ledger, AttemptLedger) or ledger.ledger_digest != self.ledger_digest:
            return False
        if tuple(commitment.slot_id for commitment in ledger.slots) != tuple(
            slot for slot, _digest in self.terminal_record_digests
        ):
            return False
        for (slot, terminal_digest), (_slot, outcome_digest) in zip(
            self.terminal_record_digests, self.outcome_object_digests
        ):
            if ledger.state(slot) not in {SlotState.RETURNED, SlotState.FAILED}:
                return False
            stored = ledger.selected_outcome(slot)
            if stored is None or stored.object_digest != outcome_digest:
                return False
            # the slot is terminal, so finalize returns its existing record and writes nothing
            if ledger.finalize(stored).record_digest != terminal_digest:
                return False
        return True

    def privacy_safe_summary(self, ledger: AttemptLedger | None = None) -> dict[str, Any]:
        if self.built_by is _BUILT_BY_EXECUTION:
            verified, how = True, "execute_prepared_study"
        elif ledger is not None and self.verify_against_ledger(ledger):
            verified, how = True, "rechecked against the ledger"
        else:
            verified, how = False, "not verified"
        result = summarize_run(self.run)
        result.update(
            {
                "attempt_ledger_digest": self.ledger_digest,
                "attempt_ledger_verified": verified,
                "attempt_ledger_verification": how,
                "outcome_object_digests": dict(self.outcome_object_digests),
                "terminal_record_digests": dict(self.terminal_record_digests),
            }
        )
        return result


def _manifest_material(
    *,
    bank: Any,
    config: EvaluationConfig,
    seeds: DesignSeeds,
    runtime_salt: bytes,
    brain_revision: str,
) -> dict[str, Any]:
    return {
        "brain_revision": brain_revision,
        "challenge_bank_seal_digest": bank.bank_seal.digest(),
        "challenge_config_digest": bank.config_digest,
        "evaluation_config_digest": config.digest,
        "executable_provenance": config.runtime.executable_provenance.to_dict(),
        "holdout_manifest_digest": bank.holdout_seal.manifest_digest,
        "holdout_payload_digest": bank.holdout_payload_digest,
        "holdout_seal_digest": bank.holdout_seal.digest(),
        "pair_count": config.pair_count,
        "retrieval_policy": "prompt_only_top3_lexical_v1",
        "runtime_salt_commitment": _sha256_bytes(runtime_salt),
        "schema": STUDY_SCHEMA,
        "schema_version": STUDY_SCHEMA_VERSION,
        "seed_commitments": seeds.commitments().to_dict(),
        "solver_prompt_digest": content_digest({"solver_prompt": SOLVER_PROMPT}),
    }


def preregister_holdout_study(
    *,
    challenge_config: str | Path,
    evaluation_config: str | Path,
    executable_path: str,
    brain_revision: str,
    secrets_material: StudySecrets,
) -> StudyPreregistration:
    """Freeze all public choices without accepting or reading holdout content."""

    if not re.fullmatch(r"[0-9a-f]{40,64}", brain_revision):
        raise ValueError("brain revision must be a lowercase Git object id")
    if not isinstance(secrets_material, StudySecrets):
        raise ValueError("study preregistration requires caller-held StudySecrets")

    bank = load_challenge_bank(challenge_config)
    base_config = EvaluationConfig.from_toml(evaluation_config)
    # This stat+hash is intentionally the last mutable runtime fact captured before
    # the evaluator-manifest digest and explicit no-improvement-use unseal request.
    provenance = capture_codex_executable_provenance(executable_path)
    config = replace(
        base_config,
        runtime=replace(base_config.runtime, executable_provenance=provenance),
    )
    manifest_digest = content_digest(
        _manifest_material(
            bank=bank,
            config=config,
            seeds=secrets_material.seeds,
            runtime_salt=secrets_material.runtime_salt,
            brain_revision=brain_revision,
        )
    )
    return StudyPreregistration(
        bank=bank,
        config=config,
        evaluator_manifest_digest=manifest_digest,
        brain_revision=brain_revision,
        executable_provenance=provenance,
    )


def open_preregistered_holdout_study(
    preregistration: StudyPreregistration,
    *,
    holdout_payload_json: str,
    secrets_material: StudySecrets,
) -> PreparedChallengeStudy:
    """Verify a durable preregistration, then open and seal the private plan."""

    if not isinstance(preregistration, StudyPreregistration):
        raise ValueError("holdout opening requires an exact StudyPreregistration")
    if not isinstance(secrets_material, StudySecrets):
        raise ValueError("holdout opening requires caller-held StudySecrets")
    if type(holdout_payload_json) is not str or not holdout_payload_json:
        raise ValueError("holdout payload must be nonempty JSON text")
    preregistration.manifest_material(secrets_material)
    bank = preregistration.bank
    config = preregistration.config
    manifest_digest = preregistration.evaluator_manifest_digest
    brain_revision = preregistration.brain_revision
    provenance = preregistration.executable_provenance
    holdout = bank.unseal_holdout(
        HoldoutUnsealRequest(
            evaluation_manifest_digest=manifest_digest,
            purpose="sealed_evaluation",
            evaluator_role="evaluator",
            acknowledge_no_improvement_use=True,
        ),
        payload_json=holdout_payload_json,
    )
    if holdout.manifest.digest() != bank.holdout_seal.manifest_digest:
        raise ValueError("opened holdout manifest differs from its public seal")

    cases = tuple(
        EvaluationCase.from_challenge(
            holdout.case(index), runtime_salt=secrets_material.runtime_salt
        )
        for index in range(holdout.case_count)
    )
    corpus = ReferenceCorpus(bank.reference_cards)
    packets: dict[str, str | None] = {}
    abstentions: dict[str, str] = {}
    audits: list[tuple[str, RetrievalAuditRecord]] = []
    for index, case in enumerate(cases):
        raw_case = holdout.case(index)
        retrieval = corpus.retrieve_for_solver_view(raw_case.solver_view(False), limit=3)
        if retrieval != corpus.retrieve_for_solver_view(
            raw_case.solver_view(True), limit=3
        ):
            raise ValueError("prompt-only retrieval changed with a hidden trace variant")
        packets[case.case_id] = retrieval.context if retrieval.card_ids else None
        if not retrieval.card_ids:
            abstentions[case.case_id] = "prompt_retrieval_abstained_v1"
        audits.append((case.case_id, retrieval.audit))

    plan = build_full_plan(
        cases,
        config=config,
        seeds=secrets_material.seeds,
        benchmark_manifest_digest=holdout.manifest.digest(),
        solver_prompt=SOLVER_PROMPT,
        brain_on_packets=packets,
        abstention_reasons=abstentions,
    )
    prepared_slots: list[PreparedSlot] = []
    for pair_dispatch in sorted(
        plan.pair_dispatch, key=lambda item: item.dispatch_index
    ):
        for condition in pair_dispatch.condition_order:
            batch = plan.batch(pair_dispatch.pair_id, condition)
            dispatch = DispatchMaterial(
                run_spec_digest=plan.spec.digest,
                batch_digest=batch.digest,
                prompt=codex_prompt_for_batch(batch),
                output_schema_json=codex_output_schema_json(batch),
                request_policy_digest=plan.spec.runtime_policy_digest,
                dispatch_nonce=batch.dispatch_nonce,
            )
            slot_id = new_slot_id()
            prepared_slots.append(
                PreparedSlot(
                    ordinal=len(prepared_slots),
                    slot_id=slot_id,
                    pair_id=pair_dispatch.pair_id,
                    condition=condition,
                    dispatch=dispatch,
                    commitment=dispatch.seal(
                        key=secrets_material.ledger_key,
                        slot_id=slot_id,
                    ),
                )
            )
    return PreparedChallengeStudy(
        plan=plan,
        evaluator_manifest_digest=manifest_digest,
        brain_revision=brain_revision,
        executable_provenance=provenance,
        slots=tuple(prepared_slots),
        retrieval_audits=tuple(audits),
    )


def prepare_holdout_study(
    *,
    challenge_config: str | Path,
    evaluation_config: str | Path,
    holdout_payload_json: str,
    executable_path: str,
    brain_revision: str,
    secrets_material: StudySecrets,
) -> PreparedChallengeStudy:
    """Convenience wrapper; production execution uses the durable two-phase API."""

    preregistration = preregister_holdout_study(
        challenge_config=challenge_config,
        evaluation_config=evaluation_config,
        executable_path=executable_path,
        brain_revision=brain_revision,
        secrets_material=secrets_material,
    )
    return open_preregistered_holdout_study(
        preregistration,
        holdout_payload_json=holdout_payload_json,
        secrets_material=secrets_material,
    )


# Treat the self-validating private codec as an opaque ledger payload.  A ``+json``
# suffix would make the generic ledger parser apply its record-string rules (which
# deliberately reject empty strings) to legitimate codec fields such as empty
# stderr bytes before the dedicated codec can apply its own schema and size checks.
_CODEX_ARTIFACT_MEDIA_TYPE = (
    "application/vnd.math-research-brain.private-codex-run-result"
)


def _execute_started_slot(
    *,
    prepared: PreparedChallengeStudy,
    slot: PreparedSlot,
    ledger: AttemptLedger,
    started: StartedAttempt,
    runtime_directory: Path,
    auth_file: Path,
) -> TerminalTransition:
    """Run one already-burned slot; never parse or score its mathematical output."""

    # Imported here so a planning-only consumer need not load the private codec.
    from .codex_artifact_io import serialize_codex_run_result

    config = prepared.plan.config
    run_config = CodexRunConfig(
        cwd=runtime_directory,
        model=config.runtime.requested_model,
        effort=config.runtime.requested_reasoning_effort,
        model_provider=config.runtime.requested_provider,
        timeout_seconds=config.timeout_seconds,
        executable=prepared.executable_provenance.canonical_path,
        auth_file=auth_file,
        max_transcript_bytes=PRIVATE_RUN_MAX_TRANSCRIPT_BYTES,
    )
    try:
        artifact = CodexAppServerRunner(run_config).run(
            prompt=slot.dispatch.prompt,
            output_schema=json.loads(slot.dispatch.output_schema_json),
        )
    except Exception:
        return ledger.record_failure(started, failure_code="runner_exception")
    try:
        encoded = serialize_codex_run_result(artifact)
        payload = ArtifactPayload(encoded, _CODEX_ARTIFACT_MEDIA_TYPE)
    except Exception:
        # The envelope is deliberately sanitized. The raw exception could contain
        # process-local paths or response fragments and does not enter the ledger.
        return ledger.record_failure(
            started,
            failure_code="artifact_packaging_exception",
        )
    return ledger.record_returned(
        started,
        payload,
    )


def execute_prepared_study(
    prepared: PreparedChallengeStudy,
    *,
    ledger: AttemptLedger,
    runtime_parent: str | Path,
    repository_root: str | Path,
    auth_file: str | Path,
    max_workers: int = 1,
) -> StudyExecution:
    """Execute exact fresh slots, persist all outcomes, then unblind and score once."""

    if not isinstance(prepared, PreparedChallengeStudy):
        raise ValueError("execution requires an exact PreparedChallengeStudy")
    if not isinstance(ledger, AttemptLedger):
        raise ValueError("execution requires an exact AttemptLedger")
    if ledger.slots != prepared.commitments:
        raise ValueError("attempt ledger differs from the prepared slot universe")
    if type(max_workers) is not int or not 1 <= max_workers <= 2:
        raise ValueError("challenge execution permits one or two concurrent solvers")
    runtime_root = Path(runtime_parent).resolve(strict=True)
    repository = Path(repository_root).resolve(strict=True)
    auth = Path(auth_file).resolve(strict=True)

    active: dict[Future[TerminalTransition], PreparedSlot] = {}
    terminals: dict[str, TerminalTransition] = {}
    next_ordinal = 0
    with ThreadPoolExecutor(
        max_workers=max_workers,
        thread_name_prefix="math-brain-solver",
    ) as executor:
        while next_ordinal < len(prepared.slots) or active:
            while next_ordinal < len(prepared.slots) and len(active) < max_workers:
                slot = prepared.slots[next_ordinal]
                # Verify all six HMAC-bound request fields before creating even an
                # empty runtime directory, then verify them again inside STARTED.
                ledger.verify_dispatch(slot.slot_id, slot.dispatch)
                runtime = ledger.allocate_runtime_directory(
                    slot.slot_id,
                    runtime_parent=runtime_root,
                    repository_root=repository,
                )
                started = ledger.start(
                    slot.slot_id,
                    runtime=runtime,
                    dispatch=slot.dispatch,
                )
                future = executor.submit(
                    _execute_started_slot,
                    prepared=prepared,
                    slot=slot,
                    ledger=ledger,
                    started=started,
                    runtime_directory=runtime.path,
                    auth_file=auth,
                )
                active[future] = slot
                next_ordinal += 1
            completed, _pending = wait(active, return_when=FIRST_COMPLETED)
            for future in completed:
                slot = active.pop(future)
                terminal = future.result()
                if terminal.slot_id != slot.slot_id:
                    raise ValueError("terminal transition belongs to another slot")
                terminals[slot.slot_id] = terminal

    # There is no parsing, treatment comparison, or score access above this line.
    # Reload every returned artifact from durable ledger bytes rather than trusting
    # the worker's now-discarded in-memory result.
    if len(terminals) != len(prepared.slots) or any(
        ledger.state(slot.slot_id) not in {SlotState.RETURNED, SlotState.FAILED}
        for slot in prepared.slots
    ):
        raise RuntimeError("not every precommitted slot reached a durable terminal state")

    from .codex_artifact_io import deserialize_codex_run_result

    batch_results = []
    runtime_evidence = []
    isolation_evidence = []
    outcome_digests = []
    terminal_digests = []
    for slot in prepared.slots:
        terminal = terminals[slot.slot_id]
        stored = ledger.selected_outcome(slot.slot_id)
        if stored is None or stored.object_digest != terminal.object_digest:
            raise RuntimeError("terminal transition is not bound to its selected outcome")
        outcome_digests.append((slot.slot_id, stored.object_digest))
        terminal_digests.append((slot.slot_id, terminal.record_digest))
        if stored.kind is OutcomeKind.RETURNED:
            payload = ledger.load_selected_artifact(slot.slot_id)
            if payload.media_type != _CODEX_ARTIFACT_MEDIA_TYPE:
                raise RuntimeError("returned slot has the wrong private artifact media type")
            artifact = deserialize_codex_run_result(payload.content)
            result, runtime, isolation = parse_codex_artifact_response(
                prepared.plan,
                pair_id=slot.pair_id,
                condition=slot.condition,
                artifact=artifact,
            )
            batch_results.append(result)
            runtime_evidence.append(runtime)
            isolation_evidence.append(isolation)
        else:
            batch_results.append(
                failed_batch(
                    prepared.plan,
                    pair_id=slot.pair_id,
                    condition=slot.condition,
                    status=AttemptStatus.INFRASTRUCTURE_FAILURE,
                    evidence_digest=stored.object_digest,
                    unblinded=True,
                )
            )
    run = finalize_run(
        prepared.plan,
        batch_results=batch_results,
        runtime_evidence=runtime_evidence,
        isolation_evidence=isolation_evidence,
        retrieval_evidence=retrieval_evidence_for_plan(prepared.plan),
    )
    return StudyExecution(
        run=run,
        ledger_digest=ledger.ledger_digest,
        terminal_record_digests=tuple(terminal_digests),
        outcome_object_digests=tuple(outcome_digests),
        built_by=_BUILT_BY_EXECUTION,
    )


def summarize_run(run: EvaluationRun) -> dict[str, Any]:
    """Return a privacy-safe numerical summary; raw prompts and transcripts stay local."""

    if not isinstance(run, EvaluationRun):
        raise ValueError("study summary requires an exact EvaluationRun")
    decision = decide_benefit(run)
    on = tuple(
        item
        for item in run.observations
        if item.assignment.condition is Condition.BRAIN_ON
    )
    off = tuple(
        item
        for item in run.observations
        if item.assignment.condition is Condition.BRAIN_OFF
    )
    on_score = aggregate(on, run.plan.config, plan=run.plan)
    off_score = aggregate(off, run.plan.config, plan=run.plan)
    return {
        "brain_off_primary": off_score.primary,
        "brain_on_primary": on_score.primary,
        "benefit_status": decision.status,
        "failed_gates": list(decision.failed_gates),
        "helped_strict": decision.helped,
        "ineligible_gates": list(decision.ineligible_gates),
        "infrastructure_complete": run.infrastructure_complete,
        "nonnegative_pairs": decision.nonnegative_pairs,
        "pair_deltas": {
            str(pair_id): value
            for pair_id, value in pair_deltas(
                run.observations, run.plan.config, plan=run.plan
            ).items()
        },
        "paired_primary_delta": decision.primary_delta,
        "pooled_primary_delta": decision.pooled_primary_delta,
        "randomization_p": decision.inference.randomization_p,
        "specificity_loss": decision.specificity_loss,
        "worst_tier_regression": decision.worst_tier_regression,
    }


__all__ = [
    "PreparedChallengeStudy",
    "PreparedSlot",
    "PRIVATE_RUN_MAX_TRANSCRIPT_BYTES",
    "SOLVER_PROMPT",
    "STUDY_SCHEMA",
    "STUDY_SCHEMA_VERSION",
    "StudySecrets",
    "StudyExecution",
    "StudyPreregistration",
    "execute_prepared_study",
    "open_preregistered_holdout_study",
    "prepare_holdout_study",
    "preregister_holdout_study",
    "summarize_run",
]
