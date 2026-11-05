import json
import re
from fractions import Fraction
from pathlib import Path

import pytest

import math_brain.challenge_bank as challenge_bank_module
from math_brain.benchmark import SolverView as BenchmarkSolverView
from math_brain.challenge_bank import (
    FAMILY_ORDER,
    HoldoutUnsealRequest,
    ReferenceCorpus,
    ShamCorpus,
    SolverView,
    TraceVariant,
    assignment_variant,
    canonical_json,
    content_digest,
    has_contiguous_token_overlap,
    load_challenge_bank,
    load_challenge_bank_json,
    opaque_case_id,
    opaque_view_digest,
    seal_config_dict,
    strict_json_loads,
    verify_witness,
)


PUBLIC_CONFIG = Path("config/challenge_bank.json")
LOCAL_HOLDOUT = Path("local/evaluation/challenge_bank_holdout.json")
RUNTIME_SALT = b"challenge-canary-salt-v1"


@pytest.fixture(scope="module")
def bank():
    return load_challenge_bank(PUBLIC_CONFIG)


def _unseal(bank):
    request = HoldoutUnsealRequest(
        evaluation_manifest_digest="sha256:" + "1" * 64,
        purpose="sealed_evaluation",
        evaluator_role="evaluator",
        acknowledge_no_improvement_use=True,
    )
    return bank.unseal_holdout(request, payload_json=LOCAL_HOLDOUT.read_text(encoding="utf-8"))


def test_public_bank_has_twenty_cards_and_development_cases_but_no_holdout_payload(bank) -> None:
    assert len(bank.reference_cards) == 20
    assert len(bank.development.case_ids) == 20
    assert bank.development.case_ids == tuple(f"D{index:02d}" for index in range(1, 21))
    assert bank.holdout_seal.case_count == 20
    assert bank.holdout_seal.family_count == 20
    assert not hasattr(bank, "holdout")
    assert not hasattr(bank, "_holdout_cases")

    raw = PUBLIC_CONFIG.read_text(encoding="utf-8")
    assert '"holdout_specs"' not in raw
    assert not re.search(r"\bH(?:0[1-9]|1[0-9]|20)\b", raw)


def test_public_descriptor_cannot_enumerate_holdout_family_truth_or_specs() -> None:
    descriptor = json.loads(PUBLIC_CONFIG.read_text(encoding="utf-8"))
    assert set(descriptor) == {
        "bank_id",
        "content_digest",
        "development_specs",
        "expected_commitments",
        "generator_version",
        "holdout_seal",
        "reference_cards",
        "schema",
        "schema_version",
    }
    assert set(descriptor["holdout_seal"]) == {
        "bank_id",
        "case_count",
        "difficulty_counts",
        "family_count",
        "manifest_digest",
        "merkle_root",
        "schema",
        "schema_version",
        "split",
    }
    public_bytes = PUBLIC_CONFIG.read_bytes()
    for forbidden in (
        b'"holdout_specs"',
        b'"truth"',
        b'"faulty_replacement"',
        b'"repair_replacement"',
        b'"witness"',
    ):
        assert forbidden not in public_bytes
    assert "local/" in Path(".gitignore").read_text(encoding="utf-8")


def test_family_order_difficulty_balance_and_single_fault_canaries(bank) -> None:
    cases = tuple(bank.development.case(case_id) for case_id in bank.development.case_ids)
    assert tuple(case.family for case in cases) == FAMILY_ORDER
    assert sum(case.difficulty.value == "medium" for case in cases) == 10
    assert sum(case.difficulty.value == "hard" for case in cases) == 10
    assert tuple(case.difficulty.value for case in cases) == ("medium",) * 10 + ("hard",) * 10
    assert {case.fault_line for case in cases} == {3}
    for case in cases:
        clean = case.trace(TraceVariant.CLEAN)
        faulty = case.trace(TraceVariant.FAULTY)
        differences = [
            line
            for line, (left, right) in enumerate(zip(clean, faulty), 1)
            if left != right
        ]
        assert differences == [case.fault_line]
        assert case.certify()
        assert case.replay(clean).accepted
        assert not case.replay(faulty).accepted


def test_all_registered_pure_verifier_kinds_are_exercised(bank) -> None:
    observed = {
        bank.development.case(case_id).witness.verifier.value
        for case_id in bank.development.case_ids
    }
    assert observed == {
        "aggregate_pointwise-v1",
        "congruence-divisibility-v1",
        "degree-sequence-v1",
        "denominator-domain-v1",
        "diagonal-v1",
        "feasible-support-point-v1",
        "finite-probability-v1",
        "finite-sum-count-v1",
        "induction-residue-v1",
        "linear-kernel-rank-nullity-v1",
        "matrix-vector-norm-v1",
        "one-sided-slope-v1",
        "polynomial-eval-v1",
        "probability-identity-v1",
        "recurrence-prefix-v1",
        "scalar-exact-rational-v1",
        "two-point-squared-ratio-v1",
    }


def test_rotation_is_balanced_by_case_and_pair() -> None:
    for case_index in range(20):
        variants = [assignment_variant(case_index, pair_id) for pair_id in range(10)]
        assert variants.count(TraceVariant.CLEAN) == 5
        assert variants.count(TraceVariant.FAULTY) == 5
    for pair_id in range(10):
        variants = [assignment_variant(case_index, pair_id) for case_index in range(20)]
        assert variants.count(TraceVariant.CLEAN) == 10
        assert variants.count(TraceVariant.FAULTY) == 10


def test_challenge_case_uses_exact_shared_solver_view_interface(bank) -> None:
    case = bank.development.case("D01")
    clean = case.solver_view(False)
    faulty = case.solver_view(True)
    assert SolverView is BenchmarkSolverView
    assert type(clean) is BenchmarkSolverView
    assert type(faulty) is BenchmarkSolverView
    assert set(clean.to_dict()) == {"numbered_solution", "prompt", "schema", "schema_version"}
    assert clean.prompt == faulty.prompt == case.prompt
    assert clean.numbered_solution != faulty.numbered_solution
    serialized = clean.canonical_json() + faulty.canonical_json()
    for forbidden in (
        case.case_id,
        case.family,
        case.difficulty.value,
        case.gold_card,
        case.retrieval_query,
        case.error_family,
    ):
        assert forbidden not in serialized


def test_opaque_helpers_are_salted_recomputable_and_view_bound(bank) -> None:
    case = bank.development.case("D07")
    view = case.solver_view(True)
    first_case_id = opaque_case_id(case, RUNTIME_SALT)
    first_view_id = opaque_view_digest(case, view, RUNTIME_SALT)
    assert first_case_id == opaque_case_id(case, RUNTIME_SALT)
    assert first_view_id == opaque_view_digest(case, view, RUNTIME_SALT)
    assert first_case_id != opaque_case_id(case, b"different-canary-salt")
    assert first_view_id != opaque_view_digest(case, view, b"different-canary-salt")
    assert case.case_id not in first_case_id
    assignment = bank.development.opaque_assignment(
        "D07", pair_id=5, runtime_salt=RUNTIME_SALT
    )
    assert type(assignment.solver_view) is BenchmarkSolverView
    with pytest.raises(ValueError, match="not bound"):
        opaque_view_digest(case, bank.development.case("D08").solver_view(False), RUNTIME_SALT)


def test_all_json_bound_witnesses_round_trip_through_registered_normalizers(bank) -> None:
    evidence = set()
    rational_cases = 0
    for case_id in bank.development.case_ids:
        case = bank.development.case(case_id)
        prefix = case.replacement_normalizer.prefix
        json_value = strict_json_loads(case.clean_replacement_line[len(prefix) :])
        json_value = strict_json_loads(canonical_json(json_value))
        normalized = case.replacement_normalizer.normalize_json_value(json_value)

        assert verify_witness(case.witness, normalized)
        passed, witness_evidence = case.verify_witness(json_value)
        assert passed
        assert witness_evidence.startswith("sha256:")
        evidence.add(witness_evidence)

        if case.replacement_normalizer.kind.value == "rational":
            rational_cases += 1
            float_alias = float(Fraction(json_value))
            assert not verify_witness(case.witness, float_alias)
            assert not case.verify_witness(float_alias)[0]

    assert rational_cases == 5
    assert len(evidence) == 20


def test_exact_repair_adapter_remains_string_bound_and_digest_only(bank) -> None:
    case = bank.development.case("D05")
    repair_passed, repair_evidence = case.verify_repair(case.clean_replacement_line)
    faulty_passed, faulty_evidence = case.verify_repair(case.faulty_replacement)
    assert repair_passed
    assert not faulty_passed
    assert repair_evidence.startswith("sha256:")
    assert faulty_evidence.startswith("sha256:")
    assert repair_evidence != faulty_evidence


def test_prompt_driven_retrieval_is_primary_and_authored_query_is_oracle_diagnostic(bank) -> None:
    corpus = ReferenceCorpus(bank.reference_cards)
    prompt_top_one = 0
    prompt_top_three = 0
    prompt_abstentions = 0
    for case_id in bank.development.case_ids:
        case = bank.development.case(case_id)
        clean = case.solver_view(False)
        faulty = case.solver_view(True)
        result = corpus.retrieve_for_solver_view(clean)
        assert result == corpus.retrieve_for_solver_view(faulty)
        assert result.audit.query_digest == content_digest({"query": case.prompt})
        assert len(result.card_ids) <= 3
        assert result.card_ids == tuple(
            card_id for card_id, _ in sorted(result.scores, key=lambda row: (-row[1], row[0]))
        )
        prompt_top_one += bool(result.card_ids) and result.card_ids[0] == case.gold_card
        prompt_top_three += case.gold_card in result.card_ids
        prompt_abstentions += not result.card_ids

        assert bank.development.oracle_retrieval_query(case_id) == case.retrieval_query
        oracle_diagnostic = corpus.retrieve(case.retrieval_query)
        assert oracle_diagnostic.card_ids[0] == case.gold_card
        assert set(result.audit.to_dict()) == {
            "abstention_digest",
            "candidate_digest",
            "context_digest",
            "query_digest",
            "returned_digest",
            "schema",
            "schema_version",
            "scores_digest",
            "trigger_digest",
        }

    assert (prompt_top_one, prompt_top_three, prompt_abstentions) == (20, 20, 0)


def test_reference_cards_have_no_twelve_token_prompt_overlap(bank) -> None:
    for card in bank.reference_cards:
        card_text = " ".join((card.title, card.guidance, *card.retrieval_cues))
        for case_id in bank.development.case_ids:
            assert not has_contiguous_token_overlap(
                card_text, bank.development.case(case_id).prompt, width=12
            )


def test_sham_corpus_returns_no_mathematics(bank) -> None:
    sham = ShamCorpus()
    query = "Check a mathematical proof obligation."
    assert sham.retrieve(query) == ()
    record = sham.audit(query)
    primary = sham.retrieve_for_solver_view(bank.development.case("D01").solver_view(False))
    assert primary.card_ids == ()
    assert primary.scores == ()
    assert primary.context == ""
    assert all(
        value.startswith("sha256:")
        for key, value in record.to_dict().items()
        if key.endswith("_digest")
    )


def test_public_config_rejects_content_or_commitment_tampering() -> None:
    raw = PUBLIC_CONFIG.read_text(encoding="utf-8")
    with pytest.raises(ValueError, match="digest mismatch"):
        load_challenge_bank_json(raw.replace('"offset": 1', '"offset": 7', 1))
    duplicate = raw.replace('"schema_version": 1,', '"schema_version": 1, "schema_version": 1,', 1)
    with pytest.raises(ValueError, match="duplicate JSON object key"):
        load_challenge_bank_json(duplicate)


def test_stale_content_digest_is_rejected_before_expensive_sealing(monkeypatch) -> None:
    raw = PUBLIC_CONFIG.read_text(encoding="utf-8").replace('"offset": 1', '"offset": 7', 1)

    def fail_if_called(_value):
        pytest.fail("expensive sealing ran before the stale content digest was rejected")

    monkeypatch.setattr(challenge_bank_module, "seal_config_dict", fail_if_called)
    with pytest.raises(ValueError, match="content digest mismatch"):
        load_challenge_bank_json(raw)


@pytest.mark.parametrize("raw", ["NaN", "Infinity", "-Infinity", "1e999"])
def test_strict_json_rejects_nonfinite_numbers(raw: str) -> None:
    with pytest.raises(ValueError, match="non-finite JSON number"):
        strict_json_loads(raw)


@pytest.mark.parametrize(
    ("case_index", "parameter", "value", "message"),
    [
        (3, "step", 257, "induction step"),
        (4, "upper", 1_000_001, "index upper bound"),
        (10, "modulus", 10_001, "modulus"),
        (12, "limit", 1_000_001, "overlap limit"),
    ],
)
def test_family_specific_bounds_precede_case_allocations(
    case_index: int, parameter: str, value: int, message: str
) -> None:
    descriptor = json.loads(PUBLIC_CONFIG.read_text(encoding="utf-8"))
    descriptor["development_specs"][case_index]["development"]["parameters"][parameter] = value
    with pytest.raises(ValueError, match=message):
        seal_config_dict(descriptor)


@pytest.mark.skipif(not LOCAL_HOLDOUT.exists(), reason="sealed holdout payload is evaluator-local")
def test_evaluator_local_holdout_opens_commitment_and_passes_all_canaries(bank) -> None:
    holdout = _unseal(bank)
    assert holdout.case_count == 20
    assert holdout.manifest.seal() == bank.holdout_seal
    corpus = ReferenceCorpus(bank.reference_cards)
    for index in range(20):
        case = holdout.case(index)
        assert case.certify()
        assert case.gold_card in corpus.retrieve(case.retrieval_query).card_ids
        card_text = " ".join(
            (
                bank.reference_cards[index].title,
                bank.reference_cards[index].guidance,
                *bank.reference_cards[index].retrieval_cues,
            )
        )
        assert not has_contiguous_token_overlap(card_text, case.prompt, width=12)


def test_holdout_unseal_fails_closed_on_wrong_payload(bank) -> None:
    request = HoldoutUnsealRequest(
        evaluation_manifest_digest="sha256:" + "2" * 64,
        purpose="sealed_evaluation",
        evaluator_role="evaluator",
        acknowledge_no_improvement_use=True,
    )
    payload = (
        LOCAL_HOLDOUT.read_text(encoding="utf-8")
        if LOCAL_HOLDOUT.exists()
        else '{"not":"a payload"}'
    )
    tampered = payload.replace('"degree": 5', '"degree": 6', 1)
    with pytest.raises(ValueError):
        bank.unseal_holdout(request, payload_json=tampered)


def test_committed_descriptor_contains_no_absolute_local_path() -> None:
    raw = PUBLIC_CONFIG.read_text(encoding="utf-8")
    assert not re.search(r"[A-Za-z]:[\\/]", raw)
    assert not re.search(r"(?:^|\s)/(?:home|Users|private|var)/", raw)
    json.loads(raw)
