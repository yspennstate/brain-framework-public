from __future__ import annotations

import hashlib
import inspect
import json
import os
import shutil
import sys
from dataclasses import dataclass, replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from math_brain import (
    external_blind_gold_audit_collect_v4 as collector,
)
from math_brain import (
    external_blind_gold_audit_v4 as audit,
)
from math_brain.attempt_ledger import ArtifactPayload, LedgerError, SlotState
from math_brain.canonical_ed25519_v3 import CanonicalEd25519SignerV3
from math_brain.codex_runner import ExecutableProvenance
from math_brain.exact_answer_v3 import ExactRationalV3
from math_brain.external_blind_gold_audit_v4 import (
    EXTERNAL_GOLD_AUDIT_VERSION_V4,
    EXTERNAL_GOLD_OUTPUT_SCHEMA_V4,
    CollectedExternalGoldAuditV4,
    ExternalGoldAttemptEvidenceV4,
    ExternalGoldAuditDecisionV4,
    ExternalGoldAuditReceiptV4,
    ExternalGoldAuditReportV4,
    ExternalGoldAuditStatusV4,
    ExternalGoldDecisionStatusV4,
    ExternalGoldReceiptStatusV4,
    PostTerminalExternalGoldVaultV4,
    RandomExternalGoldSelectionBindingV4,
    adapt_random_external_selection_v4,
    assemble_external_gold_audit_v4,
    authenticate_signed_external_gold_audit_pair_v4,
    open_external_gold_vault_after_replay_v4,
    seal_external_gold_vault_commitment_v4,
    serialize_signed_external_gold_audit_pair_v4,
    sign_external_gold_audit_receipt_v4,
    verify_external_gold_audit_report_v4,
)
from math_brain.models import canonical_json

ColdReplayCampaignV4 = audit.__dict__["ColdReplayGoldCampaignV4"]


def digest(label: str) -> str:
    return "sha256:" + hashlib.sha256(label.encode("utf-8")).hexdigest()


def digest_bytes(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def domain_digest(value: object, domain: str) -> str:
    raw = domain.encode("ascii") + b"\x00" + canonical_json(value).encode("utf-8")
    return digest_bytes(raw)


def item_id(index: int) -> str:
    suffix = hashlib.sha256(f"random external case {index}".encode()).hexdigest()
    return f"mathnet-random-v4:{suffix}"


@dataclass
class RandomFixture:
    capability: object
    audit_input: object
    selection: RandomExternalGoldSelectionBindingV4
    raw_vault: bytes
    answers: dict[str, ExactRationalV3]
    precommitment_bytes: bytes
    beacon_receipt_bytes: bytes
    precommitment_record_sha256: str
    beacon_receipt_record_sha256: str
    external_selection_precommit_anchor_evidence_digest: str
    source: object

    @property
    def replay_kwargs(self) -> dict[str, object]:
        return {
            "random_precommitment_bytes": self.precommitment_bytes,
            "random_beacon_receipt_bytes": self.beacon_receipt_bytes,
            "expected_random_precommitment_record_sha256": (self.precommitment_record_sha256),
            "expected_random_beacon_receipt_record_sha256": (self.beacon_receipt_record_sha256),
            "external_selection_precommit_anchor_evidence_digest": (
                self.external_selection_precommit_anchor_evidence_digest
            ),
            "random_source": self.source,
        }


def install_fake_random_lane(
    monkeypatch: pytest.MonkeyPatch,
    *,
    offset: int = 0,
    count: int = 12,
) -> RandomFixture:
    cases = [
        {
            "case_id": item_id(index + offset),
            "problem_text": f"Compute the exact value {index + offset + 1}.",
        }
        for index in range(count)
    ]
    answers = {
        case["case_id"]: ExactRationalV3(index + offset + 1, 1) for index, case in enumerate(cases)
    }
    projection_rows = [
        {
            "case_id": case["case_id"],
            "problem_text_digest": digest_bytes(case["problem_text"].encode("utf-8")),
        }
        for case in cases
    ]
    projection_digest = domain_digest(
        projection_rows,
        "org.math-research-brain/mathnet-random-external-gold-projection/v4",
    )
    selection_digest = digest(f"selection {offset} {count}")
    capability_digest = digest(f"capability {offset} {count}")
    gold_input_digest = digest(f"gold input {offset} {count}")
    precommitment_bytes = canonical_json({"fake_random_precommitment": [offset, count]}).encode()
    beacon_receipt_bytes = canonical_json({"fake_random_beacon_receipt": [offset, count]}).encode()
    precommitment_record_sha256 = digest_bytes(precommitment_bytes)
    beacon_receipt_record_sha256 = digest_bytes(beacon_receipt_bytes)
    external_selection_precommit_anchor_evidence_digest = digest(
        f"external selection precommit anchor evidence {offset} {count}"
    )
    source = object()

    raw_vault = canonical_json(
        {
            "entries": [
                {
                    "case_id": case["case_id"],
                    "problem_text_digest": digest_bytes(case["problem_text"].encode("utf-8")),
                    "source_answer": answers[case["case_id"]].to_dict(),
                }
                for case in cases
            ]
        }
    ).encode("utf-8")
    vault_digest = digest_bytes(raw_vault)

    class MathNetRandomExternalGoldAuditInputV4:
        def __init__(self) -> None:
            self.selection_digest = selection_digest
            self.projection_digest = projection_digest
            self.private_gold_vault_record_sha256 = vault_digest
            self.content_digest = gold_input_digest

        def validate_current(self) -> None:
            return None

    class ColdReplayMathNetRandomSelectionV4:
        def __init__(self, gold_audit_input: object) -> None:
            self.gold_audit_input = gold_audit_input
            self.selection_digest = selection_digest
            self.private_gold_vault_record_sha256 = vault_digest
            self.content_digest = capability_digest
            self.precommitment_content_digest = digest("random precommitment content")
            self.precommitment_record_sha256 = precommitment_record_sha256
            self.beacon_receipt_content_digest = digest("random beacon content")
            self.beacon_receipt_record_sha256 = beacon_receipt_record_sha256
            self.beacon_round_spec_digest = digest("random beacon round specification")
            self.beacon_round_number = 12_345
            self.beacon_anchor_payload_sha256 = digest("random beacon anchor")
            self.source_manifest_digest = digest("random source manifest")
            self.source_runtime_evidence_digest = digest("random source runtime")
            self.selector_code_manifest_digest = digest("random selector code")
            self.verified_bank_digest = digest("random verified bank")
            self.verified_bank_public_commitment_digest = digest("random public bank")
            self.selection_seed_commitment = digest("random selection seed")
            self.confirmatory_source_gate = True

        def validate_current(self) -> None:
            return None

    class FakePrivateGoldV4:
        def __init__(self, answer: ExactRationalV3) -> None:
            self.answer = answer

        def validate_current(self) -> None:
            self.answer.validate_current()

    class MathNetRandomPrivateGoldEntryV4:
        def __init__(
            self,
            *,
            case_id: str,
            problem_text_digest: str,
            source_answer: ExactRationalV3,
        ) -> None:
            self.case_id = case_id
            self.problem_text_digest = problem_text_digest
            self.private_gold = FakePrivateGoldV4(source_answer)

        def __post_init__(self) -> None:
            self.private_gold.validate_current()

    def authoritative_pair() -> tuple[object, object]:
        exact_input = MathNetRandomExternalGoldAuditInputV4()
        exact_capability = ColdReplayMathNetRandomSelectionV4(exact_input)
        return exact_capability, exact_input

    capability, audit_input = authoritative_pair()

    def external_gold_audit_projection_v4(
        observed_capability: object,
        observed_input: object,
    ) -> tuple[dict[str, str], ...]:
        assert type(observed_capability) is ColdReplayMathNetRandomSelectionV4
        assert type(observed_input) is MathNetRandomExternalGoldAuditInputV4
        assert observed_capability.gold_audit_input is observed_input
        return tuple(
            {
                "case_id": case["case_id"],
                "problem_text": case["problem_text"],
                "problem_text_digest": digest_bytes(case["problem_text"].encode("utf-8")),
            }
            for case in cases
        )

    def open_external_gold_vault_entries_after_replay_v4(
        raw: bytes,
        observed_capability: object,
        observed_input: object,
    ) -> tuple[MathNetRandomPrivateGoldEntryV4, ...]:
        assert type(observed_capability) is ColdReplayMathNetRandomSelectionV4
        assert type(observed_input) is MathNetRandomExternalGoldAuditInputV4
        assert digest_bytes(raw) == vault_digest
        parsed = json.loads(raw.decode("utf-8"))
        return tuple(
            MathNetRandomPrivateGoldEntryV4(
                case_id=item["case_id"],
                problem_text_digest=item["problem_text_digest"],
                source_answer=ExactRationalV3(
                    item["source_answer"]["numerator"],
                    item["source_answer"]["denominator"],
                ),
            )
            for item in parsed["entries"]
        )

    def reveal_and_cold_replay_mathnet_random_external_lane_v4(
        observed_precommitment: bytes,
        observed_beacon_receipt: bytes,
        *,
        expected_precommitment_record_sha256: str,
        expected_beacon_receipt_record_sha256: str,
        source: object,
    ) -> object:
        assert observed_precommitment == precommitment_bytes
        assert observed_beacon_receipt == beacon_receipt_bytes
        assert expected_precommitment_record_sha256 == precommitment_record_sha256
        assert expected_beacon_receipt_record_sha256 == beacon_receipt_record_sha256
        assert source is not None
        replayed, _replayed_input = authoritative_pair()
        return replayed

    def rebuild_mathnet_random_external_gold_vault_record_v4(
        observed_precommitment: bytes,
        observed_beacon_receipt: bytes,
        *,
        expected_precommitment_record_sha256: str,
        expected_beacon_receipt_record_sha256: str,
        source: object,
        capability: object,
        gold_input: object,
    ) -> bytes:
        assert observed_precommitment == precommitment_bytes
        assert observed_beacon_receipt == beacon_receipt_bytes
        assert expected_precommitment_record_sha256 == precommitment_record_sha256
        assert expected_beacon_receipt_record_sha256 == beacon_receipt_record_sha256
        assert source is not None
        assert type(capability) is ColdReplayMathNetRandomSelectionV4
        assert type(gold_input) is MathNetRandomExternalGoldAuditInputV4
        assert capability.gold_audit_input is gold_input
        return raw_vault

    module = SimpleNamespace(
        ColdReplayMathNetRandomSelectionV4=ColdReplayMathNetRandomSelectionV4,
        MathNetRandomExternalGoldAuditInputV4=MathNetRandomExternalGoldAuditInputV4,
        MathNetRandomPrivateGoldEntryV4=MathNetRandomPrivateGoldEntryV4,
        external_gold_audit_projection_v4=external_gold_audit_projection_v4,
        open_external_gold_vault_entries_after_replay_v4=(
            open_external_gold_vault_entries_after_replay_v4
        ),
        rebuild_mathnet_random_external_gold_vault_record_v4=(
            rebuild_mathnet_random_external_gold_vault_record_v4
        ),
        reveal_and_cold_replay_mathnet_random_external_lane_v4=(
            reveal_and_cold_replay_mathnet_random_external_lane_v4
        ),
    )
    monkeypatch.setitem(sys.modules, "math_brain.mathnet_random_external_lane_v4", module)
    selection = adapt_random_external_selection_v4(capability, audit_input)
    return RandomFixture(
        capability,
        audit_input,
        selection,
        raw_vault,
        answers,
        precommitment_bytes,
        beacon_receipt_bytes,
        precommitment_record_sha256,
        beacon_receipt_record_sha256,
        external_selection_precommit_anchor_evidence_digest,
        source,
    )


@dataclass
class CampaignFixture:
    random: RandomFixture
    material: collector.ExternalGoldPrivateMaterialV4
    prepared: collector.PreparedExternalGoldCampaignV4
    bound: collector.BoundExternalGoldCampaignV4
    repository: Path
    runtime: Path
    auth_file: Path
    anchor_signer: CanonicalEd25519SignerV3
    recovery_signer: CanonicalEd25519SignerV3
    anchor_digest: str | None
    lease: collector.ExternalGoldCampaignLeaseV4 | None
    artifacts: dict[bytes, object]


def make_campaign(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    anchor: bool = True,
    lease: bool = True,
) -> CampaignFixture:
    random = install_fake_random_lane(monkeypatch)
    material = collector.ExternalGoldPrivateMaterialV4(
        ledger_key=b"\x11" * 32,
        alias_key=b"\x22" * 32,
        vault_commitment_key=b"\x33" * 32,
        reviewer_a_seed=b"\x44" * 32,
        reviewer_b_seed=b"\x55" * 32,
        report_signing_seed=b"\x5a" * 32,
    )
    sealed = seal_external_gold_vault_commitment_v4(
        random.raw_vault,
        projection_digest=random.selection.projection_digest,
        commitment_key=material.vault_commitment_key,
    )
    repository = (tmp_path / "repo").resolve()
    runtime = (tmp_path / "runtime").resolve()
    repository.mkdir()
    runtime.mkdir()
    auth_file = (tmp_path / "auth.json").resolve()
    auth_file.write_text("{}", encoding="utf-8")
    auth_status = auth_file.stat()
    executable = str(Path(sys.executable).resolve())
    policy = collector.ExternalGoldAuditRunPolicyV4(
        executable_provenance=ExecutableProvenance(
            canonical_path=executable,
            size_bytes=max(1, Path(executable).stat().st_size),
            sha256_digest=digest("synthetic executable"),
        )
    )
    anchor_signer = CanonicalEd25519SignerV3(
        b"\x66" * 32,
        domain=collector.EXTERNAL_GOLD_ANCHOR_SIGNING_DOMAIN_V4,
    )
    recovery_signer = CanonicalEd25519SignerV3(
        b"\x77" * 32,
        domain=collector.EXTERNAL_GOLD_RECOVERY_SIGNING_DOMAIN_V4,
    )
    prepared = collector.prepare_external_gold_campaign_v4(
        random.selection,
        sealed,
        policy,
        random_selection_capability=random.capability,
        random_audit_input=random.audit_input,
        private_material=material,
        auth_snapshot=collector.ExternalGoldAuthSnapshotV4(
            canonical_path=str(auth_file),
            device=int(auth_status.st_dev),
            inode=int(auth_status.st_ino),
            size_bytes=auth_status.st_size,
            content_commitment="hmac-sha256:" + "1" * 64,
        ),
        code_identity=collector.ExternalGoldCodeIdentityV4(
            repository_root=str(repository),
            commit_oid="1" * 40,
            annotated_tag="external-blind-gold-audit-v4-test",
            tag_object_oid="2" * 40,
        ),
        anchor_verifier=anchor_signer.verifier(),
        recovery_verifier=recovery_signer.verifier(),
        **random.replay_kwargs,
    )
    bound = collector.create_external_gold_campaign_home_v4(
        prepared,
        (tmp_path / "campaign").resolve(),
        random_selection_capability=random.capability,
        random_audit_input=random.audit_input,
        **random.replay_kwargs,
    )
    anchor_digest: str | None = None
    if anchor:
        acknowledgement = collector.create_external_gold_anchor_acknowledgement_v4(
            public_precommitment_digest=bound.public_precommitment_digest,
            ledger_home_receipt_digest=bound.ledger_home_receipt_digest,
            code_commit_oid=prepared.code_identity.commit_oid,
            code_tag_object_oid=prepared.code_identity.tag_object_oid,
            anchor_namespace="git:origin/tags/external-gold-v4-test",
            anchor_object_oid="3" * 40,
            acknowledged_at_ns=1,
            signer=anchor_signer,
        )
        anchor_digest = digest_bytes(acknowledgement)
        collector.anchor_external_gold_campaign_v4(
            bound,
            acknowledgement,
            expected_acknowledgement_digest=anchor_digest,
        )
    active_lease = (
        collector.acquire_external_gold_campaign_lease_v4(bound) if anchor and lease else None
    )
    return CampaignFixture(
        random=random,
        material=material,
        prepared=prepared,
        bound=bound,
        repository=repository,
        runtime=runtime,
        auth_file=auth_file,
        anchor_signer=anchor_signer,
        recovery_signer=recovery_signer,
        anchor_digest=anchor_digest,
        lease=active_lease,
        artifacts={},
    )


def install_valid_execution(
    fixture: CampaignFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(collector, "_validate_live_inputs", lambda *args, **kwargs: b"{}")

    def fake_native_slot(
        plan: object,
        policy: collector.ExternalGoldAuditRunPolicyV4,
        auth_bytes: bytes,
        *,
        ledger: object,
        slot: collector.PreparedExternalGoldSlotV4,
        runtime_parent: Path,
        repository_root: Path,
    ):
        del auth_bytes
        decisions_by_case: dict[str, ExternalGoldAuditDecisionV4] = {}
        for binding in plan.bindings:
            decisions_by_case[binding.case_id] = ExternalGoldAuditDecisionV4(
                case_id=binding.case_id,
                status=ExternalGoldDecisionStatusV4.SOLVED,
                answer=fixture.random.answers[binding.item_id],
                verification="Checked by exact substitution.",
            )
        output_text = canonical_json(
            {
                "decisions": [
                    decisions_by_case[case_id].to_private_dict() for case_id in plan.case_order_ids
                ],
                "schema": EXTERNAL_GOLD_OUTPUT_SCHEMA_V4,
                "schema_version": EXTERNAL_GOLD_AUDIT_VERSION_V4,
            }
        )
        raw = canonical_json({"synthetic_valid_artifact": slot.ordinal}).encode("utf-8")

        def runner(runtime: Path, _dispatch: object) -> ArtifactPayload:
            status = runtime.stat()
            fixture.artifacts[raw] = SimpleNamespace(
                _raw=raw,
                prompt_text=plan.prompt_text,
                output_schema_json=plan.output_schema_json,
                requested_model=policy.requested_model,
                requested_provider=policy.requested_provider,
                requested_effort=policy.requested_effort,
                timeout_seconds=policy.timeout_seconds,
                executable_path=policy.executable_provenance.canonical_path,
                executable_size_bytes=policy.executable_provenance.size_bytes,
                executable_sha256_digest=policy.executable_provenance.sha256_digest,
                command_tail_policy_digest=policy.command_policy_digest,
                solver_process_isolation_policy_digest=policy.process_policy_digest,
                request_policy_material=collector._expected_request_policy_material(policy),
                standard_popen_used=True,
                executable_lock_enforced=True,
                codex_home_initial_auth_only=True,
                codex_home_cleanup_verified=True,
                credential_leak_scan_passed=True,
                process_exit_code=0,
                forced_termination=False,
                audit=SimpleNamespace(
                    eligible=True,
                    reasons=(),
                    accepted_model=policy.requested_model,
                    accepted_provider=policy.requested_provider,
                    accepted_effort=policy.requested_effort,
                    reroutes=(),
                    final_message_count=1,
                    model_identity_verified=False,
                ),
                output_text=output_text,
                protocol_execution_id=digest(f"protocol {slot.ordinal}"),
                execution_nonce=f"{slot.ordinal + 11:064x}",
                thread_id=f"thread-{slot.ordinal}",
                turn_id=f"turn-{slot.ordinal}",
                transcript_hash=digest(f"transcript {slot.ordinal}"),
                stderr_hash=digest(f"stderr {slot.ordinal}"),
                runtime_directory_device=int(status.st_dev),
                runtime_directory_inode=int(status.st_ino),
                codex_home_directory_device=1,
                codex_home_directory_inode=400 + slot.ordinal,
                codex_home_path_digest=digest(f"home {slot.ordinal}"),
            )
            return ArtifactPayload(raw)

        return ledger.execute_first_attempt(
            slot.slot_id,
            runtime_parent=runtime_parent,
            repository_root=repository_root,
            dispatch=slot.dispatch,
            runner=runner,
        )

    monkeypatch.setattr(collector, "_run_native_slot_v4", fake_native_slot)
    monkeypatch.setattr(
        collector,
        "deserialize_codex_run_result",
        lambda raw: fixture.artifacts[raw],
    )
    monkeypatch.setattr(
        collector,
        "serialize_codex_run_result",
        lambda artifact: artifact._raw,
    )


def collect_and_replay(
    fixture: CampaignFixture,
    monkeypatch: pytest.MonkeyPatch,
):
    assert fixture.lease is not None and fixture.anchor_digest is not None
    install_valid_execution(fixture, monkeypatch)
    pair = collector.collect_external_gold_signed_pair_bytes_v4(
        fixture.bound,
        fixture.lease,
        runtime_parent=fixture.runtime,
        repository_root=fixture.repository,
        auth_file=fixture.auth_file,
    )
    replay = collector.cold_replay_external_gold_campaign_v4(
        pair,
        bound=fixture.bound,
        lease=fixture.lease,
        expected_public_precommitment_digest=fixture.bound.public_precommitment_digest,
        expected_anchor_acknowledgement_digest=fixture.anchor_digest,
    )
    return pair, replay


def test_pre_run_state_has_only_a_sealed_vault_and_native_slot_is_narrow(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = make_campaign(tmp_path, monkeypatch, anchor=False, lease=False)
    vault_fields = {name for name in fixture.prepared.__dataclass_fields__ if "vault" in name}
    assert vault_fields == {"sealed_vault"}
    assert not any(
        hasattr(fixture.prepared, name)
        for name in ("raw", "path", "entries", "gold", "vault_raw", "vault_path")
    )
    private = collector.serialize_external_gold_private_state_v4(fixture.prepared)
    assert fixture.random.raw_vault not in private
    assert b'"entries"' not in private
    assert b'"gold"' not in private
    assert b'"source_answer"' not in private
    native_parameters = set(inspect.signature(collector._run_native_slot_v4).parameters)
    assert {"plan", "policy", "auth_bytes"} <= native_parameters
    assert not {"prepared", "vault", "sealed_vault", "private_material"} & (native_parameters)


def test_external_witness_cannot_reuse_a_reviewer_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = make_campaign(tmp_path, monkeypatch, anchor=False, lease=False)
    reused_anchor = CanonicalEd25519SignerV3(
        fixture.material.reviewer_a_seed,
        domain=collector.EXTERNAL_GOLD_ANCHOR_SIGNING_DOMAIN_V4,
    )
    with pytest.raises(ValueError, match="reviewer and witness keys must all be distinct"):
        collector.prepare_external_gold_campaign_v4(
            fixture.random.selection,
            fixture.prepared.sealed_vault,
            fixture.prepared.policy,
            random_selection_capability=fixture.random.capability,
            random_audit_input=fixture.random.audit_input,
            private_material=fixture.material,
            auth_snapshot=fixture.prepared.auth_snapshot,
            code_identity=fixture.prepared.code_identity,
            anchor_verifier=reused_anchor.verifier(),
            recovery_verifier=fixture.recovery_signer.verifier(),
            **fixture.random.replay_kwargs,
        )


def test_no_collection_or_started_record_without_external_acknowledgement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = make_campaign(tmp_path, monkeypatch, anchor=False, lease=False)
    with pytest.raises(ValueError, match="artifact cannot be read"):
        collector.acquire_external_gold_campaign_lease_v4(fixture.bound)
    ledger = fixture.bound.open_ledger()
    assert all(ledger.state(slot.slot_id) is SlotState.PENDING for slot in fixture.prepared.slots)


def test_anchor_acknowledgement_from_an_uncommitted_witness_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = make_campaign(tmp_path, monkeypatch, anchor=False, lease=False)
    wrong_signer = CanonicalEd25519SignerV3(
        b"\x99" * 32,
        domain=collector.EXTERNAL_GOLD_ANCHOR_SIGNING_DOMAIN_V4,
    )
    acknowledgement = collector.create_external_gold_anchor_acknowledgement_v4(
        public_precommitment_digest=fixture.bound.public_precommitment_digest,
        ledger_home_receipt_digest=fixture.bound.ledger_home_receipt_digest,
        code_commit_oid=fixture.prepared.code_identity.commit_oid,
        code_tag_object_oid=fixture.prepared.code_identity.tag_object_oid,
        anchor_namespace="git:origin/tags/external-gold-v4-wrong-witness",
        anchor_object_oid="3" * 40,
        acknowledged_at_ns=1,
        signer=wrong_signer,
    )
    with pytest.raises(ValueError, match="authority fields differ"):
        collector.anchor_external_gold_campaign_v4(
            fixture.bound,
            acknowledgement,
            expected_acknowledgement_digest=digest_bytes(acknowledgement),
        )
    assert not (fixture.bound.root / "state-01-anchored.json").exists()
    assert all(
        fixture.bound.open_ledger().state(slot.slot_id) is SlotState.PENDING
        for slot in fixture.prepared.slots
    )


def test_reviewer_runtime_cannot_overlap_private_campaign_home(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = make_campaign(tmp_path, monkeypatch)
    assert fixture.lease is not None
    for runtime_parent in (fixture.bound.root, fixture.bound.root.parent):
        with pytest.raises(ValueError, match="runtime parent must not contain or enter"):
            collector.collect_external_gold_signed_pair_bytes_v4(
                fixture.bound,
                fixture.lease,
                runtime_parent=runtime_parent,
                repository_root=fixture.repository,
                auth_file=fixture.auth_file,
            )
    ledger = fixture.bound.open_ledger()
    assert all(ledger.state(slot.slot_id) is SlotState.PENDING for slot in fixture.prepared.slots)


def test_campaign_artifacts_cannot_acquire_a_second_physical_name(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = make_campaign(tmp_path, monkeypatch, anchor=False, lease=False)
    private_state = fixture.bound.root / "private-state.json"
    os.link(private_state, fixture.bound.root / "private-state-hardlink.json")
    with pytest.raises(ValueError, match="one physical regular file"):
        fixture.bound.validate_current()


def test_attempt_ledger_records_cannot_acquire_a_second_physical_name(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = make_campaign(tmp_path, monkeypatch, anchor=False, lease=False)
    precommit = fixture.bound.root / "attempt-ledger" / "precommit.json"
    os.link(precommit, tmp_path / "ledger-precommit-hardlink.json")
    with pytest.raises(ValueError, match="one physical regular file"):
        fixture.bound.open_ledger()


def test_exclusive_lifecycle_lease_blocks_concurrent_collector_and_live_orphan_burn(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = make_campaign(tmp_path, monkeypatch)
    assert fixture.lease is not None
    with pytest.raises(ValueError, match="recovery is required"):
        collector.acquire_external_gold_campaign_lease_v4(fixture.bound)
    ledger = fixture.bound.open_ledger()
    slot = fixture.prepared.slots[0]
    runtime = ledger.allocate_runtime_directory(
        slot.slot_id,
        runtime_parent=fixture.runtime,
        repository_root=fixture.repository,
    )
    ledger.start(slot.slot_id, runtime=runtime, dispatch=slot.dispatch)
    with pytest.raises(ValueError, match="post-crash ownership"):
        collector.recover_orphaned_external_gold_starts_v4(
            fixture.bound,
            fixture.lease,
        )
    monkeypatch.setattr(collector, "_validate_live_inputs", lambda *args, **kwargs: b"{}")
    with pytest.raises(collector.LedgerIntegrityError, match="post-crash recovery"):
        collector.collect_external_gold_signed_pair_bytes_v4(
            fixture.bound,
            fixture.lease,
            runtime_parent=fixture.runtime,
            repository_root=fixture.repository,
            auth_file=fixture.auth_file,
        )
    assert ledger.state(slot.slot_id) is SlotState.STARTED


def test_signed_post_crash_ownership_is_required_before_orphan_recovery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = make_campaign(tmp_path, monkeypatch)
    assert fixture.lease is not None
    ledger = fixture.bound.open_ledger()
    slot = fixture.prepared.slots[0]
    runtime = ledger.allocate_runtime_directory(
        slot.slot_id,
        runtime_parent=fixture.runtime,
        repository_root=fixture.repository,
    )
    ledger.start(slot.slot_id, runtime=runtime, dispatch=slot.dispatch)
    os.close(fixture.lease.descriptor)
    fixture.lease.closed = True
    wrong_recovery_signer = CanonicalEd25519SignerV3(
        b"\x98" * 32,
        domain=collector.EXTERNAL_GOLD_RECOVERY_SIGNING_DOMAIN_V4,
    )
    wrong_ownership = collector.create_external_gold_post_crash_ownership_v4(
        public_precommitment_digest=fixture.bound.public_precommitment_digest,
        ledger_home_receipt_digest=fixture.bound.ledger_home_receipt_digest,
        stale_lifecycle_lock_digest=fixture.lease.lock_digest,
        recovery_owner_nonce="8" * 64,
        acknowledged_at_ns=2,
        signer=wrong_recovery_signer,
    )
    with pytest.raises(ValueError, match="authority fields differ"):
        collector.acquire_external_gold_recovery_lease_v4(
            fixture.bound,
            wrong_ownership,
            expected_acknowledgement_digest=digest_bytes(wrong_ownership),
        )
    ownership = collector.create_external_gold_post_crash_ownership_v4(
        public_precommitment_digest=fixture.bound.public_precommitment_digest,
        ledger_home_receipt_digest=fixture.bound.ledger_home_receipt_digest,
        stale_lifecycle_lock_digest=fixture.lease.lock_digest,
        recovery_owner_nonce="9" * 64,
        acknowledged_at_ns=2,
        signer=fixture.recovery_signer,
    )
    recovery_lease = collector.acquire_external_gold_recovery_lease_v4(
        fixture.bound,
        ownership,
        expected_acknowledgement_digest=digest_bytes(ownership),
    )
    recovered = collector.recover_orphaned_external_gold_starts_v4(
        fixture.bound,
        recovery_lease,
    )
    assert recovered == (slot.slot_id,)
    assert fixture.bound.open_ledger().state(slot.slot_id) is SlotState.FAILED
    assert fixture.anchor_digest is not None
    install_valid_execution(fixture, monkeypatch)
    pair = collector.collect_external_gold_signed_pair_bytes_v4(
        fixture.bound,
        recovery_lease,
        runtime_parent=fixture.runtime,
        repository_root=fixture.repository,
        auth_file=fixture.auth_file,
    )
    replay = collector.cold_replay_external_gold_campaign_v4(
        pair,
        bound=fixture.bound,
        lease=recovery_lease,
        expected_public_precommitment_digest=(fixture.bound.public_precommitment_digest),
        expected_anchor_acknowledgement_digest=fixture.anchor_digest,
    )
    assert replay.reviewer_a.status is ExternalGoldReceiptStatusV4.FAILED
    assert replay.reviewer_b.status is ExternalGoldReceiptStatusV4.RETURNED
    report = assemble_external_gold_audit_v4(
        replay,
        fixture.random.capability,
        fixture.random.audit_input,
        fixture.prepared.sealed_vault,
        fixture.prepared.reviewer_a_plan,
        fixture.prepared.reviewer_b_plan,
        post_terminal_vault=None,
        campaign_random_replay_evidence=fixture.prepared.random_replay_evidence,
        report_signer=fixture.material.report_signer(),
        campaign_report_verifier=fixture.prepared.report_verifier,
        report_key=b"\x88" * 32,
        **fixture.random.replay_kwargs,
    )
    assert report.status is ExternalGoldAuditStatusV4.INFEASIBLE
    assert report.admitted_count == 0
    assert report.failure_codes == ("reviewer_process_failure",)


def test_forged_signed_pair_cannot_outrun_pending_physical_ledger(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = make_campaign(tmp_path, monkeypatch)
    assert fixture.lease is not None and fixture.anchor_digest is not None
    artifact = canonical_json(
        {"detail_commitment": None, "failure_code": "runner_exception"}
    ).encode("utf-8")
    collected: list[CollectedExternalGoldAuditV4] = []
    for slot, plan in zip(fixture.prepared.slots, fixture.prepared.plans, strict=True):
        attempt = ExternalGoldAttemptEvidenceV4(
            ledger_digest=fixture.bound.attempt_ledger_digest,
            slot_id=slot.slot_id,
            ordinal=slot.ordinal,
            started_record_digest=digest(f"forged start {slot.ordinal}"),
            selection_record_digest=digest(f"forged selection {slot.ordinal}"),
            terminal_record_digest=digest(f"forged terminal {slot.ordinal}"),
            object_digest=digest(f"forged object {slot.ordinal}"),
            artifact_payload_digest=digest_bytes(artifact),
            artifact_media_type="application/json",
            terminal_kind="FAILED",
            runtime_device=1,
            runtime_inode=100 + slot.ordinal,
        )
        receipt = ExternalGoldAuditReceiptV4(
            reviewer_slot=slot.reviewer_slot,
            status=ExternalGoldReceiptStatusV4.FAILED,
            decisions=(),
            failure_code="runner_exception",
            selection_digest=plan.selection_digest,
            projection_digest=plan.projection_digest,
            order_digest=plan.order_digest,
            presentation_digest=plan.presentation_digest,
            prompt_digest=plan.prompt_digest,
            output_schema_digest=plan.output_schema_digest,
            request_policy_digest=fixture.prepared.policy.request_policy_digest,
            public_precommitment_digest=fixture.bound.public_precommitment_digest,
            anchor_acknowledgement_digest=fixture.anchor_digest,
            ledger_home_receipt_digest=fixture.bound.ledger_home_receipt_digest,
            attempt=attempt,
            run=None,
        )
        collected.append(
            CollectedExternalGoldAuditV4(
                manifest=sign_external_gold_audit_receipt_v4(
                    receipt,
                    fixture.material.signer(slot.reviewer_slot),
                ),
                artifact_bytes=artifact,
            )
        )
    pair = serialize_signed_external_gold_audit_pair_v4(collected[0], collected[1])
    (fixture.bound.root / "signed-review-pair.json").write_bytes(pair)
    with pytest.raises(collector.LedgerIntegrityError, match="not fully terminal"):
        collector.cold_replay_external_gold_campaign_v4(
            pair,
            bound=fixture.bound,
            lease=fixture.lease,
            expected_public_precommitment_digest=fixture.bound.public_precommitment_digest,
            expected_anchor_acknowledgement_digest=fixture.anchor_digest,
        )


def test_one_terminal_slot_cannot_cold_replay_or_open_gold(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = make_campaign(tmp_path, monkeypatch)
    assert fixture.lease is not None and fixture.anchor_digest is not None
    install_valid_execution(fixture, monkeypatch)
    ledger = fixture.bound.open_ledger()
    collector._run_native_slot_v4(
        fixture.prepared.reviewer_a_plan,
        fixture.prepared.policy,
        b"{}",
        ledger=ledger,
        slot=fixture.prepared.slots[0],
        runtime_parent=fixture.runtime,
        repository_root=fixture.repository,
    )
    assert ledger.state(fixture.prepared.slots[0].slot_id) is SlotState.RETURNED
    assert ledger.state(fixture.prepared.slots[1].slot_id) is SlotState.PENDING
    incomplete_pair = b"{}\n"
    (fixture.bound.root / "signed-review-pair.json").write_bytes(incomplete_pair)
    with pytest.raises(collector.LedgerIntegrityError, match="not fully terminal"):
        collector.cold_replay_external_gold_campaign_v4(
            incomplete_pair,
            bound=fixture.bound,
            lease=fixture.lease,
            expected_public_precommitment_digest=fixture.bound.public_precommitment_digest,
            expected_anchor_acknowledgement_digest=fixture.anchor_digest,
        )


def test_valid_physical_replay_then_post_terminal_vault_and_feasible_assembly(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = make_campaign(tmp_path, monkeypatch)
    _pair, replay = collect_and_replay(fixture, monkeypatch)
    opened = open_external_gold_vault_after_replay_v4(
        fixture.random.raw_vault,
        sealed_vault=fixture.prepared.sealed_vault,
        vault_commitment_key=fixture.material.vault_commitment_key,
        replay=replay,
        random_selection_capability=fixture.random.capability,
        random_audit_input=fixture.random.audit_input,
        campaign_random_replay_evidence=fixture.prepared.random_replay_evidence,
        reviewer_a_plan=fixture.prepared.reviewer_a_plan,
        reviewer_b_plan=fixture.prepared.reviewer_b_plan,
        **fixture.random.replay_kwargs,
    )
    report = assemble_external_gold_audit_v4(
        replay,
        fixture.random.capability,
        fixture.random.audit_input,
        fixture.prepared.sealed_vault,
        fixture.prepared.reviewer_a_plan,
        fixture.prepared.reviewer_b_plan,
        post_terminal_vault=opened,
        campaign_random_replay_evidence=fixture.prepared.random_replay_evidence,
        report_signer=fixture.material.report_signer(),
        campaign_report_verifier=fixture.prepared.report_verifier,
        report_key=b"\x88" * 32,
        **fixture.random.replay_kwargs,
    )
    assert report.status is ExternalGoldAuditStatusV4.FEASIBLE
    assert report.admitted_count == 12
    assert report.confirmatory_claim_authorized is False
    assert report.served_model_identity_verified is False
    assert report.provider_independence_verified is False
    assert opened.selection_digest == fixture.random.selection.selection_digest
    assert opened.signed_pair_digest == replay.signed_pair_digest
    assert opened.ledger_history_digest == replay.ledger_history_digest
    assert opened.random_gold_audit_input_digest == (
        fixture.random.selection.random_gold_audit_input_digest
    )
    assert opened.private_gold_vault_record_sha256 == digest_bytes(fixture.random.raw_vault)
    report.validate_current()
    assert report.post_terminal_vault_digest == opened.content_digest
    assert report.random_gold_audit_input_digest == (
        fixture.random.selection.random_gold_audit_input_digest
    )
    assert report.private_gold_vault_record_sha256 == opened.sealed_vault.raw_digest
    assert report.random_replay_evidence.external_selection_precommit_anchor_evidence_digest == (
        fixture.random.external_selection_precommit_anchor_evidence_digest
    )
    assert report.to_public_dict()["signed_report_digest"] == report.signed_report_digest
    verify_external_gold_audit_report_v4(
        report,
        fixture.prepared.report_verifier,
        expected_signed_report_digest=report.signed_report_digest,
    )
    wrong_report_verifier = CanonicalEd25519SignerV3(
        b"\x99" * 32,
        domain=audit.EXTERNAL_GOLD_REPORT_SIGNING_DOMAIN_V4,
    ).verifier()
    with pytest.raises(ValueError, match="campaign authority"):
        verify_external_gold_audit_report_v4(
            report,
            wrong_report_verifier,
            expected_signed_report_digest=report.signed_report_digest,
        )
    with pytest.raises(ValueError, match="differs from expected digest"):
        verify_external_gold_audit_report_v4(
            report,
            fixture.prepared.report_verifier,
            expected_signed_report_digest=digest("wrong signed report"),
        )
    object.__setattr__(report, "ledger_history_digest", digest("mutated report history"))
    with pytest.raises(ValueError, match="content digest is stale"):
        report.validate_current()


def test_exact_reviewer_agreement_that_differs_from_source_gold_admits_none(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = make_campaign(tmp_path, monkeypatch)
    changed_item = next(iter(fixture.random.answers))
    fixture.random.answers[changed_item] = ExactRationalV3(999_983, 1)
    _pair, replay = collect_and_replay(fixture, monkeypatch)
    opened = open_external_gold_vault_after_replay_v4(
        fixture.random.raw_vault,
        sealed_vault=fixture.prepared.sealed_vault,
        vault_commitment_key=fixture.material.vault_commitment_key,
        replay=replay,
        random_selection_capability=fixture.random.capability,
        random_audit_input=fixture.random.audit_input,
        campaign_random_replay_evidence=fixture.prepared.random_replay_evidence,
        reviewer_a_plan=fixture.prepared.reviewer_a_plan,
        reviewer_b_plan=fixture.prepared.reviewer_b_plan,
        **fixture.random.replay_kwargs,
    )
    report = assemble_external_gold_audit_v4(
        replay,
        fixture.random.capability,
        fixture.random.audit_input,
        fixture.prepared.sealed_vault,
        fixture.prepared.reviewer_a_plan,
        fixture.prepared.reviewer_b_plan,
        post_terminal_vault=opened,
        campaign_random_replay_evidence=fixture.prepared.random_replay_evidence,
        report_signer=fixture.material.report_signer(),
        campaign_report_verifier=fixture.prepared.report_verifier,
        report_key=b"\x88" * 32,
        **fixture.random.replay_kwargs,
    )
    assert report.status is ExternalGoldAuditStatusV4.INFEASIBLE
    assert report.admitted_count == 0
    assert report.admitted == ()
    assert report.failure_codes == ("source_gold_disagreement",)


@pytest.mark.parametrize(
    "attempt_field",
    [
        "started_record_digest",
        "selection_record_digest",
        "terminal_record_digest",
        "object_digest",
        "artifact_payload_digest",
        "runtime_inode",
    ],
)
def test_cold_replay_rejects_each_forged_history_or_runtime_binding(
    attempt_field: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = make_campaign(tmp_path, monkeypatch)
    pair, _replay = collect_and_replay(fixture, monkeypatch)
    assert fixture.lease is not None and fixture.anchor_digest is not None
    left, right = authenticate_signed_external_gold_audit_pair_v4(
        pair,
        reviewer_a_verifier=fixture.material.signer(
            fixture.prepared.slots[0].reviewer_slot
        ).verifier(),
        reviewer_b_verifier=fixture.material.signer(
            fixture.prepared.slots[1].reviewer_slot
        ).verifier(),
    )
    receipt = left.manifest.receipt
    artifact = left.artifact_bytes
    attempt_changes: dict[str, object]
    run = receipt.run
    if attempt_field == "runtime_inode":
        assert run is not None
        wrong_inode = receipt.attempt.runtime_inode + 10_000
        attempt_changes = {"runtime_inode": wrong_inode}
        run = replace(run, runtime_directory_inode=wrong_inode)
    elif attempt_field == "artifact_payload_digest":
        artifact = canonical_json({"tampered_artifact": True}).encode("utf-8")
        attempt_changes = {attempt_field: digest_bytes(artifact)}
    else:
        attempt_changes = {attempt_field: digest(f"forged {attempt_field}")}
    forged_attempt = replace(receipt.attempt, **attempt_changes)
    forged_receipt = replace(receipt, attempt=forged_attempt, run=run)
    forged_left = CollectedExternalGoldAuditV4(
        manifest=sign_external_gold_audit_receipt_v4(
            forged_receipt,
            fixture.material.signer(fixture.prepared.slots[0].reviewer_slot),
        ),
        artifact_bytes=artifact,
    )
    forged_pair = serialize_signed_external_gold_audit_pair_v4(forged_left, right)
    (fixture.bound.root / "signed-review-pair.json").write_bytes(forged_pair)
    state3_path = fixture.bound.root / "state-03-terminal.json"
    state3 = json.loads(state3_path.read_text(encoding="utf-8"))
    state3["bindings"]["signed_pair_digest"] = digest_bytes(forged_pair)
    state3_path.write_bytes((canonical_json(state3) + "\n").encode("utf-8"))
    with pytest.raises(ValueError, match="exact ledger history"):
        collector.cold_replay_external_gold_campaign_v4(
            forged_pair,
            bound=fixture.bound,
            lease=fixture.lease,
            expected_public_precommitment_digest=fixture.bound.public_precommitment_digest,
            expected_anchor_acknowledgement_digest=fixture.anchor_digest,
        )


@pytest.mark.parametrize(
    "component_pattern",
    [
        "*.started.json",
        "*.outcome-selected.json",
        "*.returned.json",
        "object",
    ],
)
def test_physical_ledger_record_mutation_fails_hmac_or_object_replay(
    component_pattern: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = make_campaign(tmp_path, monkeypatch)
    pair, _replay = collect_and_replay(fixture, monkeypatch)
    assert fixture.lease is not None and fixture.anchor_digest is not None
    ledger_root = fixture.bound.root / "attempt-ledger"
    if component_pattern == "object":
        target = next((ledger_root / "objects" / "sha256").glob("*.json"))
    else:
        target = next((ledger_root / "transitions").glob(component_pattern))
    raw = bytearray(target.read_bytes())
    raw[len(raw) // 2] ^= 1
    target.write_bytes(bytes(raw))
    with pytest.raises((ValueError, LedgerError)):
        collector.cold_replay_external_gold_campaign_v4(
            pair,
            bound=fixture.bound,
            lease=fixture.lease,
            expected_public_precommitment_digest=fixture.bound.public_precommitment_digest,
            expected_anchor_acknowledgement_digest=fixture.anchor_digest,
        )


def test_rechained_collecting_state_cannot_substitute_lifecycle_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = make_campaign(tmp_path, monkeypatch)
    pair, _replay = collect_and_replay(fixture, monkeypatch)
    assert fixture.lease is not None and fixture.anchor_digest is not None
    state2_path = fixture.bound.root / "state-02-collecting.json"
    state2 = json.loads(state2_path.read_text(encoding="utf-8"))
    state2["bindings"]["lifecycle_lock_digest"] = digest("forged lifecycle lock")
    state2_raw = (canonical_json(state2) + "\n").encode("utf-8")
    state2_path.write_bytes(state2_raw)
    state3_path = fixture.bound.root / "state-03-terminal.json"
    state3 = json.loads(state3_path.read_text(encoding="utf-8"))
    state3["prior_state_digest"] = digest_bytes(state2_raw)
    state3_path.write_bytes((canonical_json(state3) + "\n").encode("utf-8"))
    with pytest.raises(ValueError, match="collecting state differs"):
        collector.cold_replay_external_gold_campaign_v4(
            pair,
            bound=fixture.bound,
            lease=fixture.lease,
            expected_public_precommitment_digest=fixture.bound.public_precommitment_digest,
            expected_anchor_acknowledgement_digest=fixture.anchor_digest,
        )


def test_direct_capability_mints_and_one_terminal_vault_open_fail_closed() -> None:
    with pytest.raises(TypeError, match="cold replay"):
        ColdReplayCampaignV4()
    with pytest.raises(TypeError, match="exclusive acquisition"):
        collector.ExternalGoldCampaignLeaseV4()
    with pytest.raises(TypeError, match="post-terminal"):
        PostTerminalExternalGoldVaultV4()
    with pytest.raises(TypeError, match="strict replay assembly"):
        ExternalGoldAuditReportV4()
    with pytest.raises(TypeError, match="random external selection"):
        RandomExternalGoldSelectionBindingV4()


def test_legacy_or_duck_typed_selection_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    random = install_fake_random_lane(monkeypatch)
    with pytest.raises(ValueError, match="rejects non-random or legacy"):
        adapt_random_external_selection_v4(object(), random.audit_input)


def test_random_capability_and_gold_input_lineage_mismatch_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    random = install_fake_random_lane(monkeypatch)
    random.audit_input.selection_digest = digest("substituted gold input selection")
    with pytest.raises(ValueError, match="gold-audit lineage differ"):
        adapt_random_external_selection_v4(random.capability, random.audit_input)


def test_pre_beacon_anchor_evidence_cannot_be_the_precommit_record_sha(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    random = install_fake_random_lane(monkeypatch)
    with pytest.raises(ValueError, match="cannot alias a random record SHA"):
        audit._fresh_random_external_replay_v4(
            random.precommitment_bytes,
            random.beacon_receipt_bytes,
            expected_precommitment_record_sha256=(random.precommitment_record_sha256),
            expected_beacon_receipt_record_sha256=(random.beacon_receipt_record_sha256),
            external_selection_precommit_anchor_evidence_digest=(
                random.precommitment_record_sha256
            ),
            source=random.source,
            expected_capability=random.capability,
            expected_audit_input=random.audit_input,
        )


def test_object_new_forged_nominal_capability_cannot_replace_fresh_replay(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = make_campaign(tmp_path, monkeypatch, anchor=False, lease=False)
    substituted = digest("substituted random private vault")
    forged_input = object.__new__(type(fixture.random.audit_input))
    forged_input.__dict__.update(fixture.random.audit_input.__dict__)
    forged_input.private_gold_vault_record_sha256 = substituted
    forged_capability = object.__new__(type(fixture.random.capability))
    forged_capability.__dict__.update(fixture.random.capability.__dict__)
    forged_capability.gold_audit_input = forged_input
    forged_capability.private_gold_vault_record_sha256 = substituted
    selection = adapt_random_external_selection_v4(
        forged_capability,
        forged_input,
    )
    with pytest.raises(ValueError, match="differ from fresh official cold replay"):
        collector.prepare_external_gold_campaign_v4(
            selection,
            fixture.prepared.sealed_vault,
            fixture.prepared.policy,
            random_selection_capability=forged_capability,
            random_audit_input=forged_input,
            private_material=fixture.material,
            auth_snapshot=fixture.prepared.auth_snapshot,
            code_identity=fixture.prepared.code_identity,
            anchor_verifier=fixture.anchor_signer.verifier(),
            recovery_verifier=fixture.recovery_signer.verifier(),
            **fixture.random.replay_kwargs,
        )


def test_cloned_campaign_home_and_copied_pending_ledger_are_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = make_campaign(tmp_path, monkeypatch, anchor=False, lease=False)
    clone = (tmp_path / "campaign-clone").resolve()
    shutil.copytree(fixture.bound.root, clone)
    with pytest.raises(ValueError, match="another root"):
        collector.restore_external_gold_campaign_home_v4(
            fixture.prepared,
            clone,
            expected_public_precommitment_digest=(fixture.bound.public_precommitment_digest),
            random_selection_capability=fixture.random.capability,
            random_audit_input=fixture.random.audit_input,
            **fixture.random.replay_kwargs,
        )


def test_reselection_or_topup_cannot_reuse_post_terminal_capability(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = make_campaign(tmp_path, monkeypatch)
    _pair, replay = collect_and_replay(fixture, monkeypatch)
    opened = open_external_gold_vault_after_replay_v4(
        fixture.random.raw_vault,
        sealed_vault=fixture.prepared.sealed_vault,
        vault_commitment_key=fixture.material.vault_commitment_key,
        replay=replay,
        random_selection_capability=fixture.random.capability,
        random_audit_input=fixture.random.audit_input,
        campaign_random_replay_evidence=fixture.prepared.random_replay_evidence,
        reviewer_a_plan=fixture.prepared.reviewer_a_plan,
        reviewer_b_plan=fixture.prepared.reviewer_b_plan,
        **fixture.random.replay_kwargs,
    )
    replacement = install_fake_random_lane(monkeypatch, offset=20)
    with pytest.raises(ValueError, match="precommitted campaign replay"):
        assemble_external_gold_audit_v4(
            replay,
            replacement.capability,
            replacement.audit_input,
            fixture.prepared.sealed_vault,
            fixture.prepared.reviewer_a_plan,
            fixture.prepared.reviewer_b_plan,
            post_terminal_vault=opened,
            campaign_random_replay_evidence=(fixture.prepared.random_replay_evidence),
            report_signer=fixture.material.report_signer(),
            campaign_report_verifier=fixture.prepared.report_verifier,
            report_key=b"\x88" * 32,
            **replacement.replay_kwargs,
        )
    with pytest.raises(ValueError, match="exactly twelve"):
        install_fake_random_lane(monkeypatch, offset=50, count=13)
