from math_brain.models import Claim, ClaimKind, Evidence, EvidenceKind, RecordStatus
from math_brain.verification import decide_promotion


def evidence(claim: Claim, kind: EvidenceKind, *, passed: bool = True) -> Evidence:
    return Evidence(claim.id, kind, kind.value, "full stated scope", "test", passed)


def test_numeric_examples_do_not_verify_theorem() -> None:
    claim = Claim("All objects have property P", ClaimKind.THEOREM)
    decision = decide_promotion(claim, [evidence(claim, EvidenceKind.NUMERIC)], target=RecordStatus.VERIFIED)
    assert not decision.allowed


def test_lean_requires_statement_fidelity() -> None:
    claim = Claim("n + 0 = n", ClaimKind.THEOREM)
    lean_only = decide_promotion(
        claim,
        [evidence(claim, EvidenceKind.LEAN_KERNEL)],
        target=RecordStatus.VERIFIED,
    )
    full = decide_promotion(
        claim,
        [
            evidence(claim, EvidenceKind.LEAN_KERNEL),
            evidence(claim, EvidenceKind.FORMALIZATION_FIDELITY),
        ],
        target=RecordStatus.VERIFIED,
    )
    assert not lean_only.allowed
    assert full.allowed


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
