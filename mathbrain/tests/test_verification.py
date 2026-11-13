from dataclasses import asdict, replace
from pathlib import Path

import pytest

from math_brain.models import (
    Claim,
    ClaimKind,
    EdgeKind,
    Evidence,
    EvidenceAttestation,
    EvidenceAuthorityMode,
    EvidenceAuthorityRole,
    EvidenceKind,
    EvidenceReceipt,
    RecordStatus,
    VerificationEvent,
    canonical_json,
    content_hash,
)
from math_brain.store import ResearchStore
from math_brain.verification import (
    EvidenceKeyPolicy,
    EvidenceVerifier,
    decide_promotion,
    derive_and_record_status,
    evidence_authorization_mac,
    recompute_current_status,
    register_authenticated_evidence,
)


def digest(label: str) -> str:
    return content_hash({"fixture": label})


AUTO_POLICY = EvidenceKeyPolicy(
    b"automated-verifier-key-material-2026-v1",
    EvidenceAuthorityRole.AUTOMATED_VERIFIER,
    frozenset(
        {
            EvidenceKind.LEAN_KERNEL,
            EvidenceKind.NUMERIC,
            EvidenceKind.SYMBOLIC,
            EvidenceKind.EXHAUSTIVE_FINITE,
            EvidenceKind.COUNTEREXAMPLE,
        }
    ),
)
HUMAN_POLICY = EvidenceKeyPolicy(
    b"human-reviewer-key-material-2026-v1-xx",
    EvidenceAuthorityRole.HUMAN_REVIEWER,
    frozenset(
        {
            EvidenceKind.FORMALIZATION_FIDELITY,
            EvidenceKind.HUMAN_PROOF_REVIEW,
            EvidenceKind.CITATION,
        }
    ),
)
INDEPENDENT_POLICY = EvidenceKeyPolicy(
    b"independent-reproducer-key-material-v1",
    EvidenceAuthorityRole.INDEPENDENT_REPRODUCER,
    frozenset({EvidenceKind.INDEPENDENT_REPRODUCTION}),
)
MODE_FOR_ROLE = {
    EvidenceAuthorityRole.AUTOMATED_VERIFIER: EvidenceAuthorityMode.LOCAL_AUTOMATED,
    EvidenceAuthorityRole.HUMAN_REVIEWER: EvidenceAuthorityMode.LOCAL_HUMAN_REVIEW,
    EvidenceAuthorityRole.INDEPENDENT_REPRODUCER: (
        EvidenceAuthorityMode.LOCAL_INDEPENDENT_REPRODUCTION
    ),
}


def verifier() -> EvidenceVerifier:
    return EvidenceVerifier(
        {
            "automated-v1": AUTO_POLICY,
            "human-v1": HUMAN_POLICY,
            "independent-v1": INDEPENDENT_POLICY,
        }
    )


def authenticated_evidence(
    claim: Claim,
    kind: EvidenceKind,
    *,
    key_id: str,
    policy: EvidenceKeyPolicy,
    nonce: str,
    passed: bool = True,
    source: str = "formal-project-a",
    toolchain: str = "lean-a",
    scope: str = "full stated scope",
) -> tuple[Evidence, EvidenceAttestation, EvidenceReceipt]:
    # every record of one fixture shares one formal lineage (source tree, toolchain, scope) unless a test says
    # otherwise; until 30 Sep 2026 the source digest came from the evidence kind, so the kernel and fidelity records
    # of the positive tests named different source trees and still promoted
    artifact_hash = digest(f"artifact:{kind.value}:{nonce}")
    item = Evidence(
        claim.id,
        kind,
        f"authenticated {kind.value}",
        scope,
        key_id,
        passed,
        artifact_hash=artifact_hash,
    )
    verifier_digest = digest(f"verifier:{key_id}")
    toolchain_digest = digest(f"toolchain:{toolchain}")
    source_digest = digest(f"source:{source}")
    attestation = EvidenceAttestation(
        evidence_id=item.id,
        claim_id=claim.id,
        claim_digest=claim.digest(),
        evidence_digest=item.digest(),
        evidence_kind=kind,
        authority_key_id=key_id,
        authority_role=policy.role,
        authority_mode=MODE_FOR_ROLE[policy.role],
        nonce=nonce,
        artifact_hash=artifact_hash,
        request_digest=digest(f"request:{nonce}"),
        result_digest=digest(f"result:{nonce}:{passed}"),
        verifier_digest=verifier_digest,
        toolchain_digest=toolchain_digest,
        source_digest=source_digest,
        environment_digest=digest(f"environment:{key_id}"),
        passed=passed,
    )
    unsigned = EvidenceReceipt(
        evidence_id=item.id,
        claim_id=claim.id,
        claim_digest=claim.digest(),
        evidence_digest=item.digest(),
        attestation_digest=attestation.digest(),
        evidence_kind=kind,
        authority_key_id=key_id,
        authority_role=policy.role,
        nonce=nonce,
        artifact_hash=artifact_hash,
        verifier_digest=verifier_digest,
        toolchain_digest=toolchain_digest,
        source_digest=source_digest,
        mac="hmac-sha256:" + "0" * 64,
    )
    return (
        item,
        attestation,
        replace(unsigned, mac=evidence_authorization_mac(unsigned, policy.key)),
    )


def evidence(claim: Claim, kind: EvidenceKind, *, passed: bool = True) -> Evidence:
    return Evidence(claim.id, kind, kind.value, "full stated scope", "test", passed)


def test_numeric_examples_do_not_verify_theorem() -> None:
    claim = Claim("All objects have property P", ClaimKind.THEOREM)
    decision = decide_promotion(
        claim,
        [evidence(claim, EvidenceKind.NUMERIC)],
        target=RecordStatus.VERIFIED,
    )
    assert not decision.allowed


def test_lean_requires_statement_fidelity() -> None:
    claim = Claim("n + 0 = n", ClaimKind.THEOREM)
    lean = evidence(claim, EvidenceKind.LEAN_KERNEL)
    fidelity = evidence(claim, EvidenceKind.FORMALIZATION_FIDELITY)
    lean_only = decide_promotion(claim, [lean], target=RecordStatus.VERIFIED)
    unjoined = decide_promotion(claim, [lean, fidelity], target=RecordStatus.VERIFIED)
    shared = (digest("source:a"), digest("toolchain:a"), frozenset({"full stated scope"}))
    joined = decide_promotion(
        claim,
        [lean, fidelity],
        target=RecordStatus.VERIFIED,
        lineage={lean.id: shared, fidelity.id: shared},
    )
    assert not lean_only.allowed
    # without the receipts' lineage the Lean route cannot be satisfied
    assert not unjoined.allowed
    assert "lineage" in unjoined.rationale
    assert joined.allowed
    assert joined.supporting_evidence_ids == (lean.id, fidelity.id)


def test_counterexample_blocks_promotion_and_allows_refutation() -> None:
    claim = Claim("Every group is abelian", ClaimKind.THEOREM)
    counterexample = evidence(claim, EvidenceKind.COUNTEREXAMPLE)
    promote = decide_promotion(claim, [counterexample], target=RecordStatus.VERIFIED)
    refute = decide_promotion(claim, [counterexample], target=RecordStatus.REFUTED)
    assert not promote.allowed
    assert refute.allowed


def test_conjecture_must_be_restated_as_theorem() -> None:
    claim = Claim("A conjectural statement", ClaimKind.CONJECTURE)
    checks = [
        evidence(claim, EvidenceKind.HUMAN_PROOF_REVIEW),
        evidence(claim, EvidenceKind.INDEPENDENT_REPRODUCTION),
    ]
    assert not decide_promotion(claim, checks, target=RecordStatus.VERIFIED).allowed


def test_forged_evidence_receipt_cannot_persist(tmp_path: Path) -> None:
    claim = Claim("n + 0 = n", ClaimKind.THEOREM)
    item, attestation, receipt = authenticated_evidence(
        claim,
        EvidenceKind.LEAN_KERNEL,
        key_id="automated-v1",
        policy=AUTO_POLICY,
        nonce="nonce-forged-lean-0001",
    )
    replacement = "0" if receipt.mac[-1] != "0" else "1"
    forged = replace(receipt, mac=receipt.mac[:-1] + replacement)
    with ResearchStore(tmp_path / "brain.sqlite3") as store:
        store.add_claim(claim)
        with pytest.raises(ValueError, match="authorization receipt is invalid"):
            register_authenticated_evidence(
                store, item, attestation, forged, verifier()
            )
        assert store.evidence_for(claim.id) == []
        assert store.evidence_attestations_for(claim.id) == {}
        assert store.evidence_receipts_for(claim.id) == {}


def test_attestation_must_match_the_signed_runtime_record(tmp_path: Path) -> None:
    claim = Claim("n + 0 = n", ClaimKind.THEOREM)
    item, attestation, receipt = authenticated_evidence(
        claim,
        EvidenceKind.LEAN_KERNEL,
        key_id="automated-v1",
        policy=AUTO_POLICY,
        nonce="nonce-attestation-binding-0001",
    )
    substituted = replace(
        attestation,
        result_digest=digest("caller-substituted-verifier-result"),
    )
    with ResearchStore(tmp_path / "brain.sqlite3") as store:
        store.add_claim(claim)
        with pytest.raises(ValueError, match="authorization receipt is invalid"):
            register_authenticated_evidence(
                store, item, substituted, receipt, verifier()
            )
        assert store.evidence_for(claim.id) == []
        assert store.evidence_attestations_for(claim.id) == {}


def test_attestation_model_rejects_paid_networked_or_role_aliased_authority() -> None:
    claim = Claim("n + 0 = n", ClaimKind.THEOREM)
    _, attestation, _ = authenticated_evidence(
        claim,
        EvidenceKind.LEAN_KERNEL,
        key_id="automated-v1",
        policy=AUTO_POLICY,
        nonce="nonce-local-free-policy-0001",
    )
    with pytest.raises(ValueError, match="without network access"):
        replace(attestation, network_used=True)
    with pytest.raises(ValueError, match="zero marginal API cost"):
        replace(attestation, marginal_cost_microusd=1)
    with pytest.raises(ValueError, match="mode does not match"):
        replace(
            attestation,
            authority_role=EvidenceAuthorityRole.HUMAN_REVIEWER,
        )


def test_authority_role_and_key_id_are_runtime_pinned(tmp_path: Path) -> None:
    claim = Claim("n + 0 = n", ClaimKind.THEOREM)
    item, attestation, receipt = authenticated_evidence(
        claim,
        EvidenceKind.LEAN_KERNEL,
        key_id="automated-v1",
        policy=AUTO_POLICY,
        nonce="nonce-wrong-authority-0001",
    )
    wrong_role_unsigned = replace(
        receipt,
        authority_role=EvidenceAuthorityRole.HUMAN_REVIEWER,
        mac="hmac-sha256:" + "0" * 64,
    )
    wrong_role = replace(
        wrong_role_unsigned,
        mac=evidence_authorization_mac(wrong_role_unsigned, AUTO_POLICY.key),
    )
    wrong_key_unsigned = replace(
        receipt,
        authority_key_id="unconfigured-v1",
        mac="hmac-sha256:" + "0" * 64,
    )
    wrong_key = replace(
        wrong_key_unsigned,
        mac=evidence_authorization_mac(wrong_key_unsigned, AUTO_POLICY.key),
    )
    with ResearchStore(tmp_path / "brain.sqlite3") as store:
        store.add_claim(claim)
        for invalid in (wrong_role, wrong_key):
            with pytest.raises(ValueError, match="authorization receipt is invalid"):
                register_authenticated_evidence(
                    store, item, attestation, invalid, verifier()
                )
        assert store.evidence_for(claim.id) == []


def test_authority_aliases_cannot_share_key_material() -> None:
    with pytest.raises(ValueError, match="distinct key material"):
        EvidenceVerifier({"automated-a": AUTO_POLICY, "automated-b": AUTO_POLICY})


def test_discovery_report_cannot_be_authorized_or_promoted() -> None:
    claim = Claim("A reported swarm claim", ClaimKind.THEOREM)
    report = Evidence(
        claim.id,
        EvidenceKind.DISCOVERY_REPORT,
        "untrusted swarm report",
        "claim-scoped discovery",
        "proofswarm-local-import-v1",
        True,
        artifact_hash=digest("discovery-report"),
    )
    with pytest.raises(ValueError, match="discovery-only"):
        EvidenceKeyPolicy(
            b"discovery-authority-key-material-v1",
            EvidenceAuthorityRole.AUTOMATED_VERIFIER,
            frozenset({EvidenceKind.DISCOVERY_REPORT}),
        )
    decision = decide_promotion(
        claim,
        (report,),
        target=RecordStatus.PROVISIONAL,
    )
    assert not decision.allowed


def test_authenticated_lean_and_human_fidelity_promote_atomically(
    tmp_path: Path,
) -> None:
    claim = Claim("n + 0 = n", ClaimKind.THEOREM)
    lean, lean_attestation, lean_receipt = authenticated_evidence(
        claim,
        EvidenceKind.LEAN_KERNEL,
        key_id="automated-v1",
        policy=AUTO_POLICY,
        nonce="nonce-valid-lean-0001",
    )
    fidelity, fidelity_attestation, fidelity_receipt = authenticated_evidence(
        claim,
        EvidenceKind.FORMALIZATION_FIDELITY,
        key_id="human-v1",
        policy=HUMAN_POLICY,
        nonce="nonce-valid-fidelity-0001",
    )
    with ResearchStore(tmp_path / "brain.sqlite3") as store:
        store.add_claim(claim)
        register_authenticated_evidence(
            store, lean, lean_attestation, lean_receipt, verifier()
        )
        register_authenticated_evidence(
            store, fidelity, fidelity_attestation, fidelity_receipt, verifier()
        )
        applied = derive_and_record_status(
            store,
            claim.id,
            target=RecordStatus.VERIFIED,
            verifier=verifier(),
            reviewer="derived-status-test",
        )
        assert applied.decision.allowed
        assert applied.event is not None
        assert set(applied.event.evidence_ids) == {lean.id, fidelity.id}
        with pytest.raises(RuntimeError, match="must be recomputed"):
            store.current_status(claim.id)
        assert recompute_current_status(store, claim.id, verifier()) is RecordStatus.VERIFIED
        assert {edge.kind for edge in store.iter_edges()} == {EdgeKind.VERIFIED_BY}


def test_authenticated_failed_check_never_creates_a_proof_edge(tmp_path: Path) -> None:
    claim = Claim("Every group is abelian", ClaimKind.THEOREM)
    failed, attestation, receipt = authenticated_evidence(
        claim,
        EvidenceKind.COUNTEREXAMPLE,
        key_id="automated-v1",
        policy=AUTO_POLICY,
        nonce="nonce-failed-counterexample-0001",
        passed=False,
    )
    with ResearchStore(tmp_path / "brain.sqlite3") as store:
        store.add_claim(claim)
        register_authenticated_evidence(
            store, failed, attestation, receipt, verifier()
        )
        applied = derive_and_record_status(
            store,
            claim.id,
            target=RecordStatus.REFUTED,
            verifier=verifier(),
            reviewer="derived-status-test",
        )
        assert not applied.decision.allowed
        assert {edge.kind for edge in store.iter_edges()} == {
            EdgeKind.SEMANTIC_ASSOCIATION
        }


def test_untrusted_evidence_rows_cannot_drive_status(tmp_path: Path) -> None:
    claim = Claim("n + 0 = n", ClaimKind.THEOREM)
    lean = evidence(claim, EvidenceKind.LEAN_KERNEL)
    fidelity = evidence(claim, EvidenceKind.FORMALIZATION_FIDELITY)
    with ResearchStore(tmp_path / "brain.sqlite3") as store:
        store.add_claim(claim)
        store.add_evidence(lean)
        store.add_evidence(fidelity)
        applied = derive_and_record_status(
            store,
            claim.id,
            target=RecordStatus.VERIFIED,
            verifier=verifier(),
            reviewer="derived-status-test",
        )
        assert not applied.decision.allowed
        assert applied.event is None
        assert store.verification_events_for(claim.id) == []
        assert recompute_current_status(store, claim.id, verifier()) is RecordStatus.UNVERIFIED
        assert {edge.kind for edge in store.iter_edges()} == {
            EdgeKind.SEMANTIC_ASSOCIATION
        }


def test_receipt_nonce_replay_rolls_back_second_evidence(tmp_path: Path) -> None:
    claim = Claim("n + 0 = n", ClaimKind.THEOREM)
    lean, lean_attestation, lean_receipt = authenticated_evidence(
        claim,
        EvidenceKind.LEAN_KERNEL,
        key_id="automated-v1",
        policy=AUTO_POLICY,
        nonce="nonce-replay-shared-0001",
    )
    numeric, numeric_attestation, numeric_receipt = authenticated_evidence(
        claim,
        EvidenceKind.NUMERIC,
        key_id="automated-v1",
        policy=AUTO_POLICY,
        nonce="nonce-replay-shared-0001",
    )
    with ResearchStore(tmp_path / "brain.sqlite3") as store:
        store.add_claim(claim)
        register_authenticated_evidence(
            store, lean, lean_attestation, lean_receipt, verifier()
        )
        with pytest.raises(ValueError, match="not appendable"):
            register_authenticated_evidence(
                store, numeric, numeric_attestation, numeric_receipt, verifier()
            )
        assert store.evidence_for(claim.id) == [lean]
        assert set(store.evidence_attestations_for(claim.id)) == {lean.id}
        assert set(store.evidence_receipts_for(claim.id)) == {lean.id}


def test_persisted_receipt_tampering_fails_closed_on_load(tmp_path: Path) -> None:
    claim = Claim("n + 0 = n", ClaimKind.THEOREM)
    lean, attestation, receipt = authenticated_evidence(
        claim,
        EvidenceKind.LEAN_KERNEL,
        key_id="automated-v1",
        policy=AUTO_POLICY,
        nonce="nonce-tampered-receipt-0001",
    )
    with ResearchStore(tmp_path / "brain.sqlite3") as store:
        store.add_claim(claim)
        register_authenticated_evidence(
            store, lean, attestation, receipt, verifier()
        )
        payload = asdict(receipt)
        payload["source_digest"] = digest("attacker-changed-source")
        store.transaction().execute(
            "UPDATE evidence_receipts SET payload_json = ? WHERE evidence_id = ?",
            (canonical_json(payload), lean.id),
        )
        store.transaction().commit()
        with pytest.raises(ValueError, match="stored evidence receipt columns"):
            recompute_current_status(store, claim.id, verifier())


def test_persisted_attestation_substitution_fails_receipt_authentication(
    tmp_path: Path,
) -> None:
    claim = Claim("n + 0 = n", ClaimKind.THEOREM)
    lean, attestation, receipt = authenticated_evidence(
        claim,
        EvidenceKind.LEAN_KERNEL,
        key_id="automated-v1",
        policy=AUTO_POLICY,
        nonce="nonce-attestation-tamper-0001",
    )
    with ResearchStore(tmp_path / "brain.sqlite3") as store:
        store.add_claim(claim)
        register_authenticated_evidence(
            store, lean, attestation, receipt, verifier()
        )
        substituted = replace(
            attestation,
            result_digest=digest("database-substituted-result"),
        )
        store.transaction().execute(
            """UPDATE evidence_attestations SET payload_json = ?, digest = ?
               WHERE evidence_id = ?""",
            (
                canonical_json(asdict(substituted)),
                substituted.digest(),
                lean.id,
            ),
        )
        store.transaction().commit()
        with pytest.raises(ValueError, match="authorization receipt is invalid"):
            recompute_current_status(store, claim.id, verifier())


def test_missing_attestation_row_fails_closed(tmp_path: Path) -> None:
    claim = Claim("n + 0 = n", ClaimKind.THEOREM)
    lean, attestation, receipt = authenticated_evidence(
        claim,
        EvidenceKind.LEAN_KERNEL,
        key_id="automated-v1",
        policy=AUTO_POLICY,
        nonce="nonce-attestation-missing-0001",
    )
    with ResearchStore(tmp_path / "brain.sqlite3") as store:
        store.add_claim(claim)
        register_authenticated_evidence(
            store, lean, attestation, receipt, verifier()
        )
        store.transaction().execute(
            "DELETE FROM evidence_attestations WHERE evidence_id = ?",
            (lean.id,),
        )
        store.transaction().commit()
        with pytest.raises(ValueError, match="authority records are incomplete"):
            recompute_current_status(store, claim.id, verifier())


def test_receipt_binds_immutable_claim_content(tmp_path: Path) -> None:
    claim = Claim("n + 0 = n", ClaimKind.THEOREM)
    lean, attestation, receipt = authenticated_evidence(
        claim,
        EvidenceKind.LEAN_KERNEL,
        key_id="automated-v1",
        policy=AUTO_POLICY,
        nonce="nonce-claim-binding-0001",
    )
    with ResearchStore(tmp_path / "brain.sqlite3") as store:
        store.add_claim(claim)
        register_authenticated_evidence(
            store, lean, attestation, receipt, verifier()
        )
        forged_claim = replace(claim, statement="n + 0 = n + 1")
        store.transaction().execute(
            "UPDATE claims SET payload_json = ?, digest = ? WHERE id = ?",
            (
                canonical_json(asdict(forged_claim)),
                forged_claim.digest(),
                claim.id,
            ),
        )
        store.transaction().commit()
        with pytest.raises(ValueError, match="authorization receipt is invalid"):
            recompute_current_status(store, claim.id, verifier())


def test_persisted_verdict_is_recomputed_not_trusted(tmp_path: Path) -> None:
    claim = Claim("n + 0 = n", ClaimKind.THEOREM)
    lean, lean_attestation, lean_receipt = authenticated_evidence(
        claim,
        EvidenceKind.LEAN_KERNEL,
        key_id="automated-v1",
        policy=AUTO_POLICY,
        nonce="nonce-verdict-lean-0001",
    )
    fidelity, fidelity_attestation, fidelity_receipt = authenticated_evidence(
        claim,
        EvidenceKind.FORMALIZATION_FIDELITY,
        key_id="human-v1",
        policy=HUMAN_POLICY,
        nonce="nonce-verdict-fidelity-0001",
    )
    with ResearchStore(tmp_path / "brain.sqlite3") as store:
        store.add_claim(claim)
        register_authenticated_evidence(
            store, lean, lean_attestation, lean_receipt, verifier()
        )
        register_authenticated_evidence(
            store, fidelity, fidelity_attestation, fidelity_receipt, verifier()
        )
        applied = derive_and_record_status(
            store,
            claim.id,
            target=RecordStatus.VERIFIED,
            verifier=verifier(),
            reviewer="derived-status-test",
        )
        assert applied.event is not None
        forged = VerificationEvent(
            claim_id=claim.id,
            from_status=RecordStatus.UNVERIFIED,
            to_status=RecordStatus.VERIFIED,
            evidence_ids=applied.event.evidence_ids,
            rationale="caller-authored green verdict",
            reviewer=applied.event.reviewer,
            id=applied.event.id,
            created_at=applied.event.created_at,
        )
        store.transaction().execute(
            """UPDATE verification_events SET payload_json = ?, digest = ?
               WHERE id = ?""",
            (canonical_json(asdict(forged)), forged.digest(), forged.id),
        )
        store.transaction().commit()
        with pytest.raises(ValueError, match="deterministic recomputation"):
            recompute_current_status(store, claim.id, verifier())


def test_verification_event_foreign_key_tampering_fails_closed(tmp_path: Path) -> None:
    claim = Claim("n + 0 = n", ClaimKind.THEOREM)
    lean, lean_attestation, lean_receipt = authenticated_evidence(
        claim,
        EvidenceKind.LEAN_KERNEL,
        key_id="automated-v1",
        policy=AUTO_POLICY,
        nonce="nonce-event-fk-lean-0001",
    )
    fidelity, fidelity_attestation, fidelity_receipt = authenticated_evidence(
        claim,
        EvidenceKind.FORMALIZATION_FIDELITY,
        key_id="human-v1",
        policy=HUMAN_POLICY,
        nonce="nonce-event-fk-fidelity-0001",
    )
    with ResearchStore(tmp_path / "brain.sqlite3") as store:
        store.add_claim(claim)
        register_authenticated_evidence(
            store, lean, lean_attestation, lean_receipt, verifier()
        )
        register_authenticated_evidence(
            store, fidelity, fidelity_attestation, fidelity_receipt, verifier()
        )
        applied = derive_and_record_status(
            store,
            claim.id,
            target=RecordStatus.VERIFIED,
            verifier=verifier(),
            reviewer="derived-status-test",
        )
        assert applied.event is not None
        store.transaction().execute(
            """DELETE FROM verification_event_evidence
               WHERE event_id = ? AND evidence_id = ?""",
            (applied.event.id, lean.id),
        )
        store.transaction().commit()
        with pytest.raises(ValueError, match="foreign keys do not match"):
            recompute_current_status(store, claim.id, verifier())


# Formal evidence lineage join:
# a Lean kernel record and a fidelity record make one VERIFIED route only when their authenticated receipts name the
# same source tree and toolchain and their evidence the same declarations.


def _lean_route(claim: Claim, tag: str, *, lean_kw=None, fidelity_kw=None):
    lean = authenticated_evidence(
        claim,
        EvidenceKind.LEAN_KERNEL,
        key_id="automated-v1",
        policy=AUTO_POLICY,
        nonce=f"nonce-{tag}-lean-0001",
        **(lean_kw or {}),
    )
    fidelity = authenticated_evidence(
        claim,
        EvidenceKind.FORMALIZATION_FIDELITY,
        key_id="human-v1",
        policy=HUMAN_POLICY,
        nonce=f"nonce-{tag}-fidelity-0001",
        **(fidelity_kw or {}),
    )
    return lean, fidelity


def _register(store: ResearchStore, *records) -> None:
    for item, attestation, receipt in records:
        register_authenticated_evidence(store, item, attestation, receipt, verifier())


def _derive(store: ResearchStore, claim: Claim):
    return derive_and_record_status(
        store,
        claim.id,
        target=RecordStatus.VERIFIED,
        verifier=verifier(),
        reviewer="derived-status-test",
    )


@pytest.mark.parametrize(
    ("lean_kw", "fidelity_kw"),
    [
        ({"source": "generation-a"}, {"source": "generation-b"}),
        ({"toolchain": "lean-a"}, {"toolchain": "lean-b"}),
        (
            {"scope": "formal declarations: Nat.add_zero"},
            {"scope": "formal declarations: Nat.zero_add"},
        ),
    ],
    ids=["another-source-tree", "another-toolchain", "other-declarations"],
)
def test_lean_and_fidelity_of_different_lineage_do_not_promote(
    tmp_path: Path, lean_kw, fidelity_kw
) -> None:
    claim = Claim("n + 0 = n", ClaimKind.THEOREM)
    lean, fidelity = _lean_route(claim, "split", lean_kw=lean_kw, fidelity_kw=fidelity_kw)
    with ResearchStore(tmp_path / "brain.sqlite3") as store:
        store.add_claim(claim)
        _register(store, lean, fidelity)
        applied = _derive(store, claim)
        assert not applied.decision.allowed
        assert "lineage mismatch" in applied.decision.rationale
        assert applied.event is None
        assert store.verification_events_for(claim.id) == []
        assert recompute_current_status(store, claim.id, verifier()) is RecordStatus.UNVERIFIED


def test_declaration_order_does_not_split_a_lineage(tmp_path: Path) -> None:
    claim = Claim("n + 0 = n", ClaimKind.THEOREM)
    lean, fidelity = _lean_route(
        claim,
        "order",
        lean_kw={"scope": "formal declarations: Demo.a, Demo.b"},
        fidelity_kw={"scope": "formal declarations: Demo.b, Demo.a"},
    )
    with ResearchStore(tmp_path / "brain.sqlite3") as store:
        store.add_claim(claim)
        _register(store, lean, fidelity)
        assert _derive(store, claim).decision.allowed
        assert recompute_current_status(store, claim.id, verifier()) is RecordStatus.VERIFIED


def test_the_one_compatible_pair_is_chosen_among_several(tmp_path: Path) -> None:
    claim = Claim("n + 0 = n", ClaimKind.THEOREM)
    lean_a, fidelity_c = _lean_route(
        claim, "choose-ac", lean_kw={"source": "gen-a"}, fidelity_kw={"source": "gen-c"}
    )
    lean_b, fidelity_b = _lean_route(
        claim, "choose-bb", lean_kw={"source": "gen-b"}, fidelity_kw={"source": "gen-b"}
    )
    with ResearchStore(tmp_path / "brain.sqlite3") as store:
        store.add_claim(claim)
        _register(store, lean_a, fidelity_c, lean_b, fidelity_b)
        applied = _derive(store, claim)
        assert applied.decision.allowed
        assert applied.event is not None
        assert applied.event.evidence_ids == (lean_b[0].id, fidelity_b[0].id)
        assert recompute_current_status(store, claim.id, verifier()) is RecordStatus.VERIFIED


def test_one_authority_cannot_hold_both_halves_of_the_lean_route() -> None:
    with pytest.raises(ValueError, match="role does not match"):
        EvidenceKeyPolicy(
            b"one-key-for-both-halves-material-v1",
            EvidenceAuthorityRole.AUTOMATED_VERIFIER,
            frozenset({EvidenceKind.LEAN_KERNEL, EvidenceKind.FORMALIZATION_FIDELITY}),
        )


def test_a_cross_generation_event_from_before_the_join_fails_replay(tmp_path: Path) -> None:
    claim = Claim("n + 0 = n", ClaimKind.THEOREM)
    lean, fidelity = _lean_route(
        claim, "replay", lean_kw={"source": "gen-a"}, fidelity_kw={"source": "gen-b"}
    )
    with ResearchStore(tmp_path / "brain.sqlite3") as store:
        store.add_claim(claim)
        _register(store, lean, fidelity)
        # the event the route wrote before the join: the first record of each kind, whatever it spoke for
        with store.immediate_transaction():
            store._add_derived_verification_event(
                VerificationEvent(
                    claim_id=claim.id,
                    from_status=RecordStatus.UNVERIFIED,
                    to_status=RecordStatus.VERIFIED,
                    evidence_ids=(lean[0].id, fidelity[0].id),
                    rationale="evidence satisfies one complete verification route",
                    reviewer="derived-status-test",
                )
            )
        with pytest.raises(ValueError, match="deterministic recomputation"):
            recompute_current_status(store, claim.id, verifier())


def test_a_forged_from_status_alone_breaks_the_chain(tmp_path: Path) -> None:
    """1000-page report D08-T04: only the event's from_status is altered.

    Replay must refuse the chain, not trust the stored label."""
    claim = Claim("n + 0 = n", ClaimKind.THEOREM)
    lean, lean_attestation, lean_receipt = authenticated_evidence(
        claim,
        EvidenceKind.LEAN_KERNEL,
        key_id="automated-v1",
        policy=AUTO_POLICY,
        nonce="nonce-chain-lean-0001",
    )
    fidelity, fidelity_attestation, fidelity_receipt = authenticated_evidence(
        claim,
        EvidenceKind.FORMALIZATION_FIDELITY,
        key_id="human-v1",
        policy=HUMAN_POLICY,
        nonce="nonce-chain-fidelity-0001",
    )
    with ResearchStore(tmp_path / "brain.sqlite3") as store:
        store.add_claim(claim)
        register_authenticated_evidence(store, lean, lean_attestation, lean_receipt, verifier())
        register_authenticated_evidence(
            store, fidelity, fidelity_attestation, fidelity_receipt, verifier()
        )
        applied = derive_and_record_status(
            store,
            claim.id,
            target=RecordStatus.VERIFIED,
            verifier=verifier(),
            reviewer="derived-status-test",
        )
        assert applied.event is not None
        assert applied.event.from_status is RecordStatus.UNVERIFIED
        forged = replace(applied.event, from_status=RecordStatus.PROVISIONAL)
        store.transaction().execute(
            """UPDATE verification_events SET payload_json = ?, digest = ?
               WHERE id = ?""",
            (canonical_json(asdict(forged)), forged.digest(), forged.id),
        )
        store.transaction().commit()
        with pytest.raises(ValueError, match="broken status chain"):
            recompute_current_status(store, claim.id, verifier())
