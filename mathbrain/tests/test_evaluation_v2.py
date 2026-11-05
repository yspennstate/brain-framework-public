from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from itertools import repeat
from types import SimpleNamespace

import pytest

from math_brain.benchmark import SolverView
from math_brain.challenge_bank import assignment_variant
from math_brain.evaluation import Condition, Variant, Verdict
from math_brain.evaluation_v2 import (
    V2_PAIR_COUNT,
    V2ActiveControlPlan,
    V2BoundVerifier,
    V2CaseSpec,
    V2ContextCard,
    V2GateConfig,
    V2HardCaseBinding,
    V2Outcome,
    V2ReceiptAuthority,
    V2RenderedView,
    V2VerifierKind,
    assignment_variant_v2,
    build_v2_plan,
    full_case_success_v2,
    precommit_v2_design,
    solver_answer_digest_v2,
    summarize_v2,
)


def _sha(label: str) -> str:
    return "sha256:" + hashlib.sha256(label.encode()).hexdigest()


def _case(
    index: int,
    *,
    unique_strata: bool = False,
    shared_difficulty: bool = False,
    crossed_family_template: bool = False,
) -> V2CaseSpec:
    stratum_index = index if unique_strata else index % 4
    subgroup_index = (
        index
        if unique_strata
        else ((index % 4 + index // 4) % 4 if crossed_family_template else index % 4)
    )
    return V2CaseSpec(
        case_id=f"public-case-{index:02d}",
        domain=f"domain-{stratum_index:02d}",
        difficulty="shared-tier" if shared_difficulty else f"tier-{index % 4}",
        mechanism=f"mechanism-{stratum_index:02d}",
        template_id=f"template-{subgroup_index:02d}",
        clean_view=V2RenderedView(
            prompt=f"Audit public synthetic argument {index:02d}.",
            numbered_solution=("1. establish the premise", "2. exact conclusion"),
        ),
        faulty_view=V2RenderedView(
            prompt=f"Audit public synthetic argument {index:02d}.",
            numbered_solution=("1. establish the premise", "2. subtle invalid conclusion"),
        ),
        root_localization=f"root-node-{index:02d}",
        error_family=f"family-{subgroup_index:02d}",
        repair_verifier_digest=_sha(f"repair-verifier-{index:02d}"),
        witness_verifier_digest=_sha(f"witness-verifier-{index:02d}"),
    )


def _public_design(
    count: int = 20,
    gate: V2GateConfig | None = None,
    *,
    unique_strata: bool = False,
    shared_difficulty: bool = False,
    crossed_family_template: bool = False,
):
    cases = tuple(
        _case(
            index,
            unique_strata=unique_strata,
            shared_difficulty=shared_difficulty,
            crossed_family_template=crossed_family_template,
        )
        for index in range(count)
    )
    # All texts have exactly the same UTF-8 length.  Unique relevance keys make every
    # non-identity edge relevance-disjoint, so the control is a genuine derangement.
    contexts = tuple(
        V2ContextCard(
            case_id=case.case_id,
            text=f"abstract control card {index:02d}",
            relevance_keys=case.required_relevance_keys,
        )
        for index, case in enumerate(cases)
    )
    assignment_seed = hashlib.sha256(b"public-v2-assignment-seed").digest()
    control_seed = hashlib.sha256(b"public-v2-control-seed").digest()
    authority = V2ReceiptAuthority(hashlib.sha256(b"public-v2-receipt-authority").digest())
    commitment = precommit_v2_design(
        cases,
        contexts,
        assignment_seed=assignment_seed,
        control_seed=control_seed,
        receipt_authority_digest=authority.digest,
        gate=gate,
    )
    plan = build_v2_plan(
        cases,
        contexts,
        assignment_seed=assignment_seed,
        control_seed=control_seed,
        commitment=commitment,
        gate=gate,
    )
    return cases, contexts, assignment_seed, control_seed, authority, commitment, plan


def _bound_verifier(case: V2CaseSpec, kind: V2VerifierKind, *, accepted: bool = True):
    verifier_digest = (
        case.repair_verifier_digest
        if kind is V2VerifierKind.REPAIR
        else case.witness_verifier_digest
    )
    return V2BoundVerifier(
        kind,
        verifier_digest,
        lambda candidate: (
            accepted,
            _sha(
                f"replay-evidence-{kind.value}-{accepted}-{json.dumps(candidate, sort_keys=True)}"
            ),
        ),
    )


def _passing_outcome(assignment, authority, case: V2CaseSpec | None = None):
    case_index = int(assignment.case_id.rsplit("-", 1)[1])
    case = case or _case(case_index)
    if assignment.variant is Variant.CLEAN:
        return V2Outcome.finalize(
            assignment,
            case,
            verdict=Verdict.VALID,
            root_localization=None,
            error_family=None,
            repair_candidate=None,
            witness_candidate=None,
            receipt_authority=authority,
        )
    return V2Outcome.finalize(
        assignment,
        case,
        verdict=Verdict.INVALID,
        root_localization=case.root_localization,
        error_family=case.error_family,
        repair_candidate={"replacement_line": f"repair-{case_index:02d}"},
        witness_candidate={"witness": case_index},
        receipt_authority=authority,
        repair_verifier=_bound_verifier(case, V2VerifierKind.REPAIR),
        witness_verifier=_bound_verifier(case, V2VerifierKind.INDEPENDENT_WITNESS),
    )


def _failing_outcome(assignment, authority, case: V2CaseSpec | None = None):
    case_index = int(assignment.case_id.rsplit("-", 1)[1])
    case = case or _case(case_index)
    if assignment.variant is Variant.FAULTY:
        return V2Outcome.finalize(
            assignment,
            case,
            verdict=Verdict.VALID,
            root_localization=None,
            error_family=None,
            repair_candidate=None,
            witness_candidate=None,
            receipt_authority=authority,
        )
    return V2Outcome.finalize(
        assignment,
        case,
        verdict=Verdict.INVALID,
        root_localization="wrong-root",
        error_family="wrong-family",
        repair_candidate=None,
        witness_candidate=None,
        receipt_authority=authority,
    )


def test_twenty_pair_assignment_is_deterministic_balanced_and_variable() -> None:
    cases, contexts, assignment_seed, control_seed, _, commitment, plan = _public_design()
    rebuilt = build_v2_plan(
        tuple(reversed(cases)),
        tuple(reversed(contexts)),
        assignment_seed=assignment_seed,
        control_seed=control_seed,
        commitment=commitment,
    )
    assert rebuilt.digest == plan.digest
    assert V2_PAIR_COUNT == 20
    assert {assignment.pair_id for assignment in plan.assignments} == set(range(20))

    for case in cases:
        variants = [assignment_variant_v2(plan, case.case_id, pair_id) for pair_id in range(20)]
        assert variants.count(Variant.CLEAN) == 10
        assert variants.count(Variant.FAULTY) == 10

    counts = plan.faulty_counts_by_pair
    assert min(counts) == 7
    assert max(counts) == 13
    assert len(set(counts)) > 2
    assert all(7 <= count <= 13 for count in counts)


@pytest.mark.parametrize("seed_index", range(24))
def test_seeded_degree_construction_preserves_every_invariant(seed_index: int) -> None:
    cases, contexts, _, control_seed, authority, _, _ = _public_design()
    assignment_seed = hashlib.sha256(f"assignment-{seed_index}".encode()).digest()
    commitment = precommit_v2_design(
        cases,
        contexts,
        assignment_seed=assignment_seed,
        control_seed=control_seed,
        receipt_authority_digest=authority.digest,
    )
    plan = build_v2_plan(
        cases,
        contexts,
        assignment_seed=assignment_seed,
        control_seed=control_seed,
        commitment=commitment,
    )
    assert all(count in range(7, 14) for count in plan.faulty_counts_by_pair)
    assert len(set(plan.faulty_counts_by_pair)) > 1
    for case in cases:
        assert (
            sum(plan.variant_for(case.case_id, pair) is Variant.FAULTY for pair in range(20)) == 10
        )


@pytest.mark.parametrize("seed_index", range(8))
def test_current_thirty_two_case_canary_has_exact_v2_degree_contract(seed_index: int) -> None:
    cases, contexts, _, control_seed, authority, _, _ = _public_design(count=32)
    assignment_seed = hashlib.sha256(f"canary-32-{seed_index}".encode()).digest()
    commitment = precommit_v2_design(
        cases,
        contexts,
        assignment_seed=assignment_seed,
        control_seed=control_seed,
        receipt_authority_digest=authority.digest,
    )
    plan = build_v2_plan(
        cases,
        contexts,
        assignment_seed=assignment_seed,
        control_seed=control_seed,
        commitment=commitment,
    )
    assert min(plan.faulty_counts_by_pair) == 12
    assert max(plan.faulty_counts_by_pair) == 20
    assert all(
        sum(plan.variant_for(case.case_id, pair) is Variant.FAULTY for pair in range(20)) == 10
        for case in cases
    )


def test_seed_reveal_and_all_evaluator_inputs_are_bound_to_precommitment() -> None:
    cases, contexts, assignment_seed, control_seed, _, commitment, _ = _public_design()
    with pytest.raises(ValueError, match="precommitment"):
        build_v2_plan(
            cases,
            contexts,
            assignment_seed=hashlib.sha256(b"changed assignment seed").digest(),
            control_seed=control_seed,
            commitment=commitment,
        )
    with pytest.raises(ValueError, match="precommitment"):
        build_v2_plan(
            cases,
            contexts,
            assignment_seed=assignment_seed,
            control_seed=control_seed,
            commitment=commitment,
            gate=V2GateConfig(minimum_mean_case_delta=0.041),
        )
    changed_case = replace(cases[0], root_localization="post-hoc-root")
    with pytest.raises(ValueError, match="precommitment"):
        build_v2_plan(
            (changed_case, *cases[1:]),
            contexts,
            assignment_seed=assignment_seed,
            control_seed=control_seed,
            commitment=commitment,
        )
    changed_context = replace(contexts[0], text="post-hoc context text 00")
    with pytest.raises(ValueError, match="precommitment"):
        build_v2_plan(
            cases,
            (changed_context, *contexts[1:]),
            assignment_seed=assignment_seed,
            control_seed=control_seed,
            commitment=commitment,
        )


def test_plan_rejects_all_assignment_views_replaced_without_commitment_change() -> None:
    *_, plan = _public_design()
    assert len(plan.assignments) == 800
    assert plan.assignment_matrix_digest == plan.commitment.assignment_payload_digest
    easy_view = plan.cases[0].clean_view
    forged_assignments = tuple(
        replace(assignment, view=easy_view, view_digest=easy_view.digest)
        for assignment in plan.assignments
    )
    with pytest.raises(ValueError, match="committed case variant"):
        replace(plan, assignments=forged_assignments)


def test_conditions_have_identical_cases_order_variants_and_view_commitments() -> None:
    *_, plan = _public_design()
    for pair_id in range(20):
        on = plan.assignments_for(pair_id, Condition.BRAIN_ON)
        off = plan.assignments_for(pair_id, Condition.BRAIN_OFF)
        assert [item.case_id for item in on] == [item.case_id for item in off]
        assert [item.variant for item in on] == [item.variant for item in off]
        assert [item.order_index for item in on] == [item.order_index for item in off]
        assert [item.solver_case_id for item in on] == [item.solver_case_id for item in off]
        assert [item.view for item in on] == [item.view for item in off]
        assert [item.view_digest for item in on] == [item.view_digest for item in off]
        assert [item.matched_input_digest for item in on] == [
            item.matched_input_digest for item in off
        ]
        on_batch = plan.solver_batch(pair_id, Condition.BRAIN_ON)
        off_batch = plan.solver_batch(pair_id, Condition.BRAIN_OFF)
        assert on_batch.matched_cases_digest == off_batch.matched_cases_digest
        assert [case.view.to_dict() for case in on_batch.cases] == [
            case.view.to_dict() for case in off_batch.cases
        ]


def test_active_control_is_exact_length_relevance_disjoint_derangement() -> None:
    cases, contexts, *_, plan = _public_design()
    context_by_id = {card.case_id: card for card in contexts}
    sources = set()
    for binding in plan.active_control.bindings:
        target = context_by_id[binding.target_case_id]
        source = context_by_id[binding.source_case_id]
        assert binding.target_case_id != binding.source_case_id
        assert target.byte_length == source.byte_length == binding.byte_length
        assert set(target.relevance_keys).isdisjoint(source.relevance_keys)
        assert binding.relevant_digest == target.text_digest
        assert binding.control_digest == source.text_digest
        sources.add(binding.source_case_id)
    assert sources == {case.case_id for case in cases}


def test_plan_rejects_twenty_of_twenty_relevance_overlapping_controls() -> None:
    _, contexts, *_, plan = _public_design()
    context_by_id = {card.case_id: card for card in contexts}
    ids = [card.case_id for card in contexts]
    forged_bindings = []
    for binding in plan.active_control.bindings:
        target_index = ids.index(binding.target_case_id)
        source = context_by_id[ids[(target_index + 4) % len(ids)]]
        target = context_by_id[binding.target_case_id]
        forged_bindings.append(
            replace(
                binding,
                source_case_id=source.case_id,
                control_digest=source.text_digest,
                byte_length=target.byte_length,
            )
        )
    forged_control = V2ActiveControlPlan(
        tuple(forged_bindings), plan.active_control.control_seed_commitment
    )
    assert sum(
        not set(context_by_id[binding.target_case_id].relevance_keys).isdisjoint(
            context_by_id[binding.source_case_id].relevance_keys
        )
        for binding in forged_control.bindings
    ) == len(contexts)
    with pytest.raises(ValueError, match="payload differs from its precommitment"):
        replace(plan, active_control=forged_control)
    forged_commitment = replace(
        plan.commitment, active_control_payload_digest=forged_control.digest
    )
    with pytest.raises(ValueError, match=r"relevance-disjoint|design commitment"):
        replace(plan, active_control=forged_control, commitment=forged_commitment)


def test_active_control_rejects_length_or_relevance_leakage() -> None:
    cases, contexts, assignment_seed, control_seed, authority, _, _ = _public_design()
    impossible = tuple(
        replace(
            card,
            relevance_keys=tuple(sorted((*card.relevance_keys, "shared-topic"))),
        )
        for card in contexts
    )
    with pytest.raises(ValueError, match=r"no .*exact-length relevance-disjoint control"):
        precommit_v2_design(
            cases,
            impossible,
            assignment_seed=assignment_seed,
            control_seed=control_seed,
            receipt_authority_digest=authority.digest,
        )


def test_solver_projection_omits_truth_variant_and_control_provenance() -> None:
    cases, contexts, *_, plan = _public_design()
    on = plan.solver_batch(19, Condition.BRAIN_ON)
    off = plan.solver_batch(19, Condition.BRAIN_OFF)
    assert on.matched_cases_digest == off.matched_cases_digest
    assert on.treatment_digest != off.treatment_digest
    encoded = json.dumps(
        {"on": on.to_solver_payload(), "off": off.to_solver_payload()},
        sort_keys=True,
    )
    for forbidden_key in (
        '"condition"',
        '"variant"',
        '"source_case_id"',
        '"relevance_keys"',
        '"root_localization"',
        '"error_family"',
        '"clean_view_digest"',
        '"faulty_view_digest"',
    ):
        assert forbidden_key not in encoded
    for case in cases:
        assert case.case_id not in encoded
        assert case.root_localization not in encoded
        assert case.error_family not in encoded
        assert case.clean_view.prompt in encoded
    # Context text is the intended treatment; its evaluator-side source identity is not.
    assert all(card.text in encoded for card in contexts)


def test_rendered_view_adapter_is_exact_and_rejects_private_extra_fields() -> None:
    clean = SolverView(
        prompt="Audit the exact public argument.",
        numbered_solution=("1. premise", "2. exact conclusion"),
    )
    faulty = SolverView(
        prompt="Audit the exact public argument.",
        numbered_solution=("1. premise", "2. subtle invalid conclusion"),
    )
    adapter = SimpleNamespace(
        case_id="adapter-case",
        domain="algebra",
        difficulty="hard",
        mechanism="hidden-domain-loss",
        template_id="adapter-template",
        clean_solver_view=clean,
        faulty_solver_view=faulty,
        root_localization="2",
        error_family="illegal-cancellation",
        repair_verifier_digest=_sha("adapter-repair"),
        witness_verifier_digest=_sha("adapter-witness"),
        verify_repair=lambda proposal: (proposal == "exact-repair", _sha("repair-replay")),
        verify_witness=lambda payload: (payload == {"witness": 1}, _sha("witness-replay")),
    )
    case = V2CaseSpec.from_hard_case(adapter)
    binding = V2HardCaseBinding.from_hard_case(adapter)
    assert case.clean_view.to_dict() == clean.to_dict()
    assert case.faulty_view.to_dict() == faulty.to_dict()
    assert case.clean_view.digest == _sha(clean.canonical_json())
    assert binding.case == case
    assert binding.repair.verify("exact-repair") == (True, _sha("repair-replay"))
    assert binding.witness.verify({"witness": 1}) == (True, _sha("witness-replay"))
    with pytest.raises(ValueError, match="exactly four public fields"):
        V2RenderedView.from_mapping({**clean.to_dict(), "answer": "private"})


def test_context_must_include_committed_domain_family_mechanism_and_template_keys() -> None:
    cases, contexts, assignment_seed, control_seed, authority, _, _ = _public_design()
    missing = replace(
        contexts[0],
        relevance_keys=tuple(
            key for key in contexts[0].relevance_keys if not key.startswith("mechanism:")
        ),
    )
    with pytest.raises(ValueError, match="required canonical relevance keys"):
        precommit_v2_design(
            cases,
            (missing, *contexts[1:]),
            assignment_seed=assignment_seed,
            control_seed=control_seed,
            receipt_authority_digest=authority.digest,
        )


def test_full_case_primary_is_binary_and_requires_every_fault_component() -> None:
    cases, _, _, _, authority, _, plan = _public_design()
    case_by_id = {case.case_id: case for case in cases}
    clean = next(item for item in plan.assignments if item.variant is Variant.CLEAN)
    faulty = next(item for item in plan.assignments if item.variant is Variant.FAULTY)
    clean_outcome = _passing_outcome(clean, authority)
    faulty_outcome = _passing_outcome(faulty, authority)
    assert (
        full_case_success_v2(
            clean,
            case_by_id[clean.case_id],
            clean_outcome,
            receipt_authority=authority,
        )
        is True
    )
    assert (
        full_case_success_v2(
            faulty,
            case_by_id[faulty.case_id],
            faulty_outcome,
            receipt_authority=authority,
        )
        is True
    )
    assert (
        full_case_success_v2(
            clean,
            case_by_id[clean.case_id],
            _failing_outcome(clean, authority),
            receipt_authority=authority,
        )
        is False
    )
    assert (
        full_case_success_v2(
            faulty,
            case_by_id[faulty.case_id],
            _failing_outcome(faulty, authority),
            receipt_authority=authority,
        )
        is False
    )

    faulty_case = case_by_id[faulty.case_id]

    def finalized_faulty(
        *,
        root=faulty_case.root_localization,
        family=faulty_case.error_family,
        repair_accepted: bool = True,
        witness_accepted: bool = True,
    ):
        return V2Outcome.finalize(
            faulty,
            faulty_case,
            verdict=Verdict.INVALID,
            root_localization=root,
            error_family=family,
            repair_candidate=faulty_outcome.repair_candidate,
            witness_candidate=faulty_outcome.witness_candidate,
            receipt_authority=authority,
            repair_verifier=_bound_verifier(
                faulty_case, V2VerifierKind.REPAIR, accepted=repair_accepted
            ),
            witness_verifier=_bound_verifier(
                faulty_case,
                V2VerifierKind.INDEPENDENT_WITNESS,
                accepted=witness_accepted,
            ),
        )

    broken = (
        finalized_faulty(root="nearby-not-root"),
        finalized_faulty(family="nearby-family"),
        replace(faulty_outcome, repair_receipt=None),
        replace(faulty_outcome, witness_receipt=None),
        finalized_faulty(repair_accepted=False),
        finalized_faulty(witness_accepted=False),
        replace(
            faulty_outcome,
            witness_receipt=replace(
                faulty_outcome.witness_receipt,
                verifier_digest=faulty_outcome.repair_receipt.verifier_digest,
            ),
        ),
    )
    assert all(
        not full_case_success_v2(
            faulty,
            case_by_id[faulty.case_id],
            outcome,
            receipt_authority=authority,
        )
        for outcome in broken
    )
    assert set(
        full_case_success_v2(
            faulty,
            case_by_id[faulty.case_id],
            outcome,
            receipt_authority=authority,
        )
        for outcome in broken
    ) == {False}


def test_valid_outcome_cannot_smuggle_partial_fault_credit() -> None:
    cases, _, _, _, authority, _, plan = _public_design()
    case_by_id = {case.case_id: case for case in cases}
    clean = next(item for item in plan.assignments if item.variant is Variant.CLEAN)
    with pytest.raises(ValueError, match="exact VALID"):
        V2Outcome.finalize(
            clean,
            case_by_id[clean.case_id],
            verdict=Verdict.VALID,
            root_localization="guessed-root",
            error_family=None,
            repair_candidate=None,
            witness_candidate=None,
            receipt_authority=authority,
        )


def test_solver_cannot_forge_evaluator_receipt_credit() -> None:
    cases, _, _, _, authority, _, plan = _public_design()
    case_by_id = {case.case_id: case for case in cases}
    faulty = next(item for item in plan.assignments if item.variant is Variant.FAULTY)
    authentic = _passing_outcome(faulty, authority)
    rejected = authority.issue(
        faulty,
        case_by_id[faulty.case_id],
        kind=V2VerifierKind.REPAIR,
        candidate=authentic.repair_candidate,
        solver_answer_digest=authentic.solver_answer_digest,
        verifier=_bound_verifier(case_by_id[faulty.case_id], V2VerifierKind.REPAIR, accepted=False),
    )
    with pytest.raises(ValueError, match="differs from the case precommitment"):
        authority.issue(
            faulty,
            case_by_id[faulty.case_id],
            kind=V2VerifierKind.REPAIR,
            candidate={"candidate": "forged"},
            solver_answer_digest=authentic.solver_answer_digest,
            verifier=V2BoundVerifier(
                V2VerifierKind.REPAIR,
                case_by_id[faulty.case_id].witness_verifier_digest,
                lambda _candidate: (True, _sha("forged-evidence")),
            ),
        )
    forged = replace(rejected, accepted=True)
    assert authority.verifies(rejected)
    assert not authority.verifies(forged)
    forged_outcome = replace(authentic, repair_receipt=forged)
    assert not full_case_success_v2(
        faulty,
        case_by_id[faulty.case_id],
        forged_outcome,
        receipt_authority=authority,
    )
    with pytest.raises(ValueError, match="solver-answer digest differs"):
        replace(authentic, repair_candidate={"replacement_line": "oracle substitution"})
    clean = next(item for item in plan.assignments if item.variant is Variant.CLEAN)
    with pytest.raises(ValueError, match="forbidden for clean"):
        authority.issue(
            clean,
            case_by_id[clean.case_id],
            kind=V2VerifierKind.REPAIR,
            candidate={"replacement_line": "should never be verified"},
            solver_answer_digest=_passing_outcome(clean, authority).solver_answer_digest,
            verifier=_bound_verifier(case_by_id[clean.case_id], V2VerifierKind.REPAIR),
        )
    wrong_authority = V2ReceiptAuthority(hashlib.sha256(b"wrong authority").digest())
    assert not wrong_authority.verifies(authentic.repair_receipt)
    with pytest.raises(ValueError, match="receipt authority differs"):
        summarize_v2(
            plan,
            tuple(_passing_outcome(item, authority) for item in plan.assignments),
            receipt_authority=wrong_authority,
        )


def test_stale_answer_digest_cannot_turn_a_wrong_root_into_credit() -> None:
    cases, _, _, _, authority, _, plan = _public_design()
    case_by_id = {case.case_id: case for case in cases}
    faulty = next(item for item in plan.assignments if item.variant is Variant.FAULTY)
    case = case_by_id[faulty.case_id]
    wrong_root = V2Outcome.finalize(
        faulty,
        case,
        verdict=Verdict.INVALID,
        root_localization="wrong-root",
        error_family=case.error_family,
        repair_candidate={"replacement_line": "valid repair"},
        witness_candidate={"witness": "valid witness"},
        receipt_authority=authority,
        repair_verifier=_bound_verifier(case, V2VerifierKind.REPAIR),
        witness_verifier=_bound_verifier(case, V2VerifierKind.INDEPENDENT_WITNESS),
    )
    assert not full_case_success_v2(
        faulty,
        case,
        wrong_root,
        receipt_authority=authority,
    )

    # Frozen dataclasses can still be altered through object.__setattr__.  Scoring
    # must reconstruct the answer digest instead of trusting the old sealed value.
    object.__setattr__(wrong_root, "root_localization", case.root_localization)
    with pytest.raises(ValueError, match="solver-answer digest differs"):
        full_case_success_v2(
            faulty,
            case,
            wrong_root,
            receipt_authority=authority,
        )


def test_post_construction_verifier_callback_swap_cannot_mint_credit() -> None:
    cases, _, _, _, authority, _, plan = _public_design()
    case_by_id = {case.case_id: case for case in cases}
    faulty = next(item for item in plan.assignments if item.variant is Variant.FAULTY)
    case = case_by_id[faulty.case_id]
    verifier = _bound_verifier(case, V2VerifierKind.REPAIR, accepted=False)
    object.__setattr__(
        verifier,
        "replay",
        lambda _candidate: (True, _sha("swapped-callback-evidence")),
    )
    with pytest.raises(ValueError, match="replay capability changed"):
        V2Outcome.finalize(
            faulty,
            case,
            verdict=Verdict.INVALID,
            root_localization=case.root_localization,
            error_family=case.error_family,
            repair_candidate={"replacement_line": "plausible"},
            witness_candidate={"witness": "plausible"},
            receipt_authority=authority,
            repair_verifier=verifier,
            witness_verifier=_bound_verifier(
                case,
                V2VerifierKind.INDEPENDENT_WITNESS,
            ),
        )


def test_verifier_callback_and_code_identity_cannot_change_after_binding() -> None:
    def reject(_candidate):
        return False, _sha("original-rejection")

    def accept(_candidate):
        return True, _sha("mutated-acceptance")

    verifier = V2BoundVerifier(
        V2VerifierKind.REPAIR,
        _sha("semantic-verifier"),
        reject,
    )
    reject.__code__ = accept.__code__
    with pytest.raises(ValueError, match="replay capability changed"):
        verifier.verify({"candidate": "unchanged"})

    class MutableReplay:
        def __call__(self, _candidate):
            return False, _sha("mutable-replay")

    with pytest.raises(ValueError, match="exact Python function or bound method"):
        V2BoundVerifier(
            V2VerifierKind.REPAIR,
            _sha("mutable-verifier"),
            MutableReplay(),
        )

    class UndigestedBoundState:
        def replay(self, _candidate):
            return False, _sha("undigested-bound-state")

    with pytest.raises(ValueError, match="method state must expose a canonical digest"):
        V2BoundVerifier(
            V2VerifierKind.REPAIR,
            _sha("undigested-bound-verifier"),
            UndigestedBoundState().replay,
        )


def test_receipt_revalidates_case_before_comparison_and_candidate_after_replay() -> None:
    class HostileText(str):
        def __ne__(self, _other):
            raise AssertionError("hostile inequality must never run before exact validation")

    cases, _, _, _, authority, _, plan = _public_design()
    case_by_id = {case.case_id: case for case in cases}
    faulty = next(item for item in plan.assignments if item.variant is Variant.FAULTY)
    case = case_by_id[faulty.case_id]
    verifier = _bound_verifier(case, V2VerifierKind.REPAIR)
    object.__setattr__(case, "case_id", HostileText(case.case_id))
    with pytest.raises(ValueError, match="case id must be a string"):
        authority.issue(
            faulty,
            case,
            kind=V2VerifierKind.REPAIR,
            candidate={"candidate": "unchanged"},
            solver_answer_digest=_sha("bounded answer"),
            verifier=verifier,
        )

    cases, _, _, _, authority, _, plan = _public_design()
    case_by_id = {case.case_id: case for case in cases}
    faulty = next(item for item in plan.assignments if item.variant is Variant.FAULTY)
    case = case_by_id[faulty.case_id]

    def mutating_replay(candidate):
        candidate["mutated"] = True
        return True, _sha("mutating-replay-evidence")

    verifier = V2BoundVerifier(
        V2VerifierKind.REPAIR,
        case.repair_verifier_digest,
        mutating_replay,
    )
    with pytest.raises(ValueError, match="mutated its candidate"):
        authority.issue(
            faulty,
            case,
            kind=V2VerifierKind.REPAIR,
            candidate={"candidate": "original"},
            solver_answer_digest=_sha("bounded answer"),
            verifier=verifier,
        )

    cases, _, _, _, authority, _, plan = _public_design()
    case_by_id = {case.case_id: case for case in cases}
    faulty = next(item for item in plan.assignments if item.variant is Variant.FAULTY)
    case = case_by_id[faulty.case_id]

    def truth_mutating_replay(_candidate):
        object.__setattr__(case, "root_localization", "mutated-but-well-formed-root")
        return True, _sha("truth-mutating-replay-evidence")

    verifier = V2BoundVerifier(
        V2VerifierKind.REPAIR,
        case.repair_verifier_digest,
        truth_mutating_replay,
    )
    with pytest.raises(ValueError, match="mutated sealed evaluator state"):
        authority.issue(
            faulty,
            case,
            kind=V2VerifierKind.REPAIR,
            candidate={"candidate": "original"},
            solver_answer_digest=_sha("bounded answer"),
            verifier=verifier,
        )


def test_ties_never_count_toward_strict_positive_case_gates() -> None:
    _, _, _, _, authority, _, plan = _public_design(count=40)
    outcomes = tuple(_passing_outcome(assignment, authority) for assignment in plan.assignments)
    summary = summarize_v2(
        plan,
        outcomes,
        receipt_authority=authority,
    )
    assert summary.mean_case_delta == 0
    assert summary.strictly_positive_cases == 0
    assert summary.tied_cases == 40
    assert summary.negative_cases == 0
    assert summary.strictly_positive_faulty_cases == 0
    assert "strictly_positive_cases" in summary.failed_gates
    assert "exact_case_randomization_p" in summary.failed_gates
    assert "case_interval_lower_bound" in summary.failed_gates
    assert "median_case_delta" in summary.failed_gates
    assert "faulty_mean_case_delta" in summary.failed_gates


@pytest.mark.parametrize(
    ("field_name", "weaker_value"),
    (
        ("minimum_mean_case_delta", 0.039),
        ("minimum_strictly_positive_cases", 11),
        ("maximum_exact_case_randomization_p", 0.051),
        ("minimum_mean_pair_delta", 0.039),
        ("minimum_strictly_positive_pairs", 11),
        ("maximum_exact_pair_randomization_p", 0.051),
        ("minimum_mean_faulty_case_delta", 0.039),
        ("minimum_strictly_positive_faulty_cases", 11),
        ("maximum_exact_faulty_case_randomization_p", 0.051),
        ("minimum_mean_faulty_pair_delta", 0.039),
        ("minimum_strictly_positive_faulty_pairs", 11),
        ("maximum_exact_faulty_pair_randomization_p", 0.051),
        ("case_interval_confidence", 0.90),
        ("case_bootstrap_repetitions", 4095),
        ("maximum_clean_specificity_loss", 0.021),
        ("maximum_faulty_populated_stratum_regression", 0.051),
        ("minimum_populated_stratum_cases", 7),
        ("minimum_difficulty_stratum_cases", 7),
        ("minimum_claim_case_count", 39),
        ("maximum_case_count", 513),
        ("minimum_brain_off_success_for_valid_benchmark", 0.29),
        ("maximum_brain_off_success_for_valid_benchmark", 0.86),
        ("minimum_faulty_brain_off_success_for_valid_benchmark", 0.29),
        ("maximum_faulty_brain_off_success_for_valid_benchmark", 0.86),
        ("minimum_difficulty_stratum_brain_off_success", 0.29),
        ("maximum_difficulty_stratum_brain_off_success", 0.86),
        ("minimum_faulty_stratum_brain_off_success", 0.29),
        ("maximum_faulty_stratum_brain_off_success", 0.86),
    ),
)
def test_required_benefit_and_difficulty_gates_cannot_be_relaxed(
    field_name: str, weaker_value: float
) -> None:
    with pytest.raises(ValueError):
        replace(V2GateConfig(), **{field_name: weaker_value})


def test_twenty_repeats_of_two_effect_cases_are_only_two_independent_units() -> None:
    cases, _, _, _, authority, _, plan = _public_design(count=40)
    baseline_failures = {case.case_id for case in cases[2:8]}
    effect_cases = {case.case_id for case in cases[:2]}
    outcomes = []
    for assignment in plan.assignments:
        should_fail = assignment.case_id in baseline_failures or (
            assignment.case_id in effect_cases and assignment.condition is Condition.BRAIN_OFF
        )
        outcomes.append(
            _failing_outcome(assignment, authority)
            if should_fail
            else _passing_outcome(assignment, authority)
        )
    summary = summarize_v2(plan, outcomes, receipt_authority=authority)
    assert summary.mean_case_delta == 0.05
    assert summary.strictly_positive_cases == 2
    assert summary.tied_cases == 38
    assert summary.exact_case_randomization_p == 0.25
    assert summary.exact_case_randomization_p != pytest.approx(2**-20)
    assert "strictly_positive_cases" in summary.failed_gates
    assert "exact_case_randomization_p" in summary.failed_gates
    assert summary.helped is False


def test_clean_gains_cannot_compensate_for_faulty_trace_degradation() -> None:
    _, _, _, _, authority, _, plan = _public_design(count=40)
    faulty_on_failures = set()
    for case in plan.cases:
        faulty_on = sorted(
            (
                assignment
                for assignment in plan.assignments
                if assignment.case_id == case.case_id
                and assignment.condition is Condition.BRAIN_ON
                and assignment.variant is Variant.FAULTY
            ),
            key=lambda assignment: assignment.pair_id,
        )
        faulty_on_failures.update(assignment.digest for assignment in faulty_on[:2])
    outcomes = []
    for assignment in plan.assignments:
        should_fail = (
            assignment.condition is Condition.BRAIN_OFF and assignment.variant is Variant.CLEAN
        ) or assignment.digest in faulty_on_failures
        outcomes.append(
            _failing_outcome(assignment, authority)
            if should_fail
            else _passing_outcome(assignment, authority)
        )
    summary = summarize_v2(plan, outcomes, receipt_authority=authority)
    assert summary.mean_case_delta == 0.4
    assert summary.exact_case_randomization_p == pytest.approx(2**-40)
    assert summary.mean_faulty_case_delta == -0.2
    assert summary.negative_faulty_cases == 40
    assert "faulty_mean_case_delta" in summary.failed_gates
    assert "faulty_populated_stratum_regression" in summary.failed_gates
    assert summary.helped is False


def test_each_supported_domain_and_mechanism_difficulty_stratum_is_bounded() -> None:
    cases, _, _, _, authority, _, plan = _public_design(count=40, shared_difficulty=True)
    case_by_id = {case.case_id: case for case in cases}
    outcomes = []
    for assignment in plan.assignments:
        stratum_index = int(case_by_id[assignment.case_id].domain.rsplit("-", 1)[1])
        should_fail = assignment.condition is Condition.BRAIN_OFF and stratum_index >= 2
        committed_case = case_by_id[assignment.case_id]
        outcomes.append(
            _failing_outcome(assignment, authority, committed_case)
            if should_fail
            else _passing_outcome(assignment, authority, committed_case)
        )
    summary = summarize_v2(plan, outcomes, receipt_authority=authority)
    assert summary.brain_off_success == 0.5
    assert summary.minimum_difficulty_stratum_brain_off_success_observed == 0.5
    assert summary.maximum_difficulty_stratum_brain_off_success_observed == 0.5
    assert summary.minimum_populated_stratum_brain_off_success_observed == 0
    assert summary.maximum_populated_stratum_brain_off_success_observed == 1
    assert "populated_stratum_difficulty_floor" in summary.failed_gates
    assert "populated_stratum_difficulty_ceiling" in summary.failed_gates
    assert summary.helped is False


def test_clean_success_cannot_hide_an_impossible_faulty_brain_off_benchmark() -> None:
    cases, _, _, _, authority, _, plan = _public_design(
        count=40,
        shared_difficulty=True,
        crossed_family_template=True,
    )
    case_by_id = {case.case_id: case for case in cases}
    outcomes = tuple(
        _failing_outcome(assignment, authority, case_by_id[assignment.case_id])
        if assignment.condition is Condition.BRAIN_OFF and assignment.variant is Variant.FAULTY
        else _passing_outcome(assignment, authority, case_by_id[assignment.case_id])
        for assignment in plan.assignments
    )
    summary = summarize_v2(plan, outcomes, receipt_authority=authority)
    assert summary.clean_success_brain_off == 1
    assert summary.faulty_success_brain_off == 0
    assert summary.brain_off_success == 0.5
    assert summary.minimum_populated_stratum_brain_off_success_observed == 0.5
    assert summary.maximum_populated_stratum_brain_off_success_observed == 0.5
    assert summary.minimum_faulty_populated_stratum_brain_off_success_observed == 0
    assert summary.maximum_faulty_populated_stratum_brain_off_success_observed == 0
    assert summary.faulty_ceiling.brain_off_errors == 400
    assert summary.faulty_ceiling.brain_off_total == 400
    assert summary.faulty_ceiling.benchmark_too_hard is True
    assert "benchmark_floor" not in summary.failed_gates
    assert "populated_stratum_difficulty_floor" not in summary.failed_gates
    assert "faulty_benchmark_floor" in summary.failed_gates
    assert "faulty_populated_stratum_difficulty_floor" in summary.failed_gates
    assert summary.helped is False


def test_each_supported_faulty_stratum_has_its_own_floor_and_ceiling() -> None:
    cases, _, _, _, authority, _, plan = _public_design(
        count=40,
        shared_difficulty=True,
        crossed_family_template=True,
    )
    case_by_id = {case.case_id: case for case in cases}
    clean_failures: set[str] = set()
    for case in cases:
        domain_index = int(case.domain.rsplit("-", 1)[1])
        if domain_index >= 2:
            continue
        clean_off = sorted(
            (
                assignment
                for assignment in plan.assignments
                if assignment.case_id == case.case_id
                and assignment.condition is Condition.BRAIN_OFF
                and assignment.variant is Variant.CLEAN
            ),
            key=lambda assignment: assignment.pair_id,
        )
        clean_failures.update(assignment.digest for assignment in clean_off[:5])

    outcomes = []
    for assignment in plan.assignments:
        domain_index = int(case_by_id[assignment.case_id].domain.rsplit("-", 1)[1])
        should_fail = assignment.condition is Condition.BRAIN_OFF and (
            (assignment.variant is Variant.FAULTY and domain_index >= 2)
            or assignment.digest in clean_failures
        )
        outcomes.append(
            _failing_outcome(assignment, authority, case_by_id[assignment.case_id])
            if should_fail
            else _passing_outcome(assignment, authority, case_by_id[assignment.case_id])
        )

    summary = summarize_v2(plan, outcomes, receipt_authority=authority)
    assert summary.brain_off_success == 0.625
    assert summary.faulty_success_brain_off == 0.5
    assert summary.minimum_populated_stratum_brain_off_success_observed == 0.5
    assert summary.maximum_populated_stratum_brain_off_success_observed == 0.75
    assert summary.minimum_faulty_populated_stratum_brain_off_success_observed == 0
    assert summary.maximum_faulty_populated_stratum_brain_off_success_observed == 1
    assert "benchmark_floor" not in summary.failed_gates
    assert "benchmark_ceiling" not in summary.failed_gates
    assert "faulty_benchmark_floor" not in summary.failed_gates
    assert "faulty_benchmark_ceiling" not in summary.failed_gates
    assert "populated_stratum_difficulty_floor" not in summary.failed_gates
    assert "populated_stratum_difficulty_ceiling" not in summary.failed_gates
    assert "faulty_populated_stratum_difficulty_floor" in summary.failed_gates
    assert "faulty_populated_stratum_difficulty_ceiling" in summary.failed_gates
    assert summary.helped is False


def test_family_and_template_difficulty_cannot_hide_aggregate_half_rate() -> None:
    cases, _, _, _, authority, _, plan = _public_design(
        count=40,
        shared_difficulty=True,
        crossed_family_template=True,
    )
    case_by_id = {case.case_id: case for case in cases}
    assert {case.error_family for case in cases} == {
        "family-00",
        "family-01",
        "family-02",
        "family-03",
    }
    assert {case.template_id for case in cases} == {
        "template-00",
        "template-01",
        "template-02",
        "template-03",
    }
    outcomes = []
    for assignment in plan.assignments:
        family_index = int(case_by_id[assignment.case_id].error_family.rsplit("-", 1)[1])
        should_fail = assignment.condition is Condition.BRAIN_OFF and family_index >= 2
        case = case_by_id[assignment.case_id]
        outcomes.append(
            _failing_outcome(assignment, authority, case)
            if should_fail
            else _passing_outcome(assignment, authority, case)
        )
    summary = summarize_v2(plan, outcomes, receipt_authority=authority)
    assert summary.brain_off_success == 0.5
    assert summary.minimum_difficulty_stratum_brain_off_success_observed == 0.5
    assert summary.maximum_difficulty_stratum_brain_off_success_observed == 0.5
    assert summary.minimum_populated_stratum_brain_off_success_observed == 0
    assert summary.maximum_populated_stratum_brain_off_success_observed == 1
    assert summary.adequately_populated_stratum_count == 16
    assert "populated_stratum_difficulty_floor" in summary.failed_gates
    assert "populated_stratum_difficulty_ceiling" in summary.failed_gates
    assert summary.helped is False


def test_localized_family_and_template_faulty_harm_cannot_be_averaged_away() -> None:
    cases, _, _, _, authority, _, plan = _public_design(
        count=40,
        shared_difficulty=True,
        crossed_family_template=True,
    )
    case_by_id = {case.case_id: case for case in cases}
    failing_digests = set()
    for case in cases:
        for condition in Condition:
            clean = sorted(
                (
                    assignment
                    for assignment in plan.assignments
                    if assignment.case_id == case.case_id
                    and assignment.condition is condition
                    and assignment.variant is Variant.CLEAN
                ),
                key=lambda assignment: assignment.pair_id,
            )
            failing_digests.update(assignment.digest for assignment in clean[:4])
    for assignment in plan.assignments:
        if assignment.variant is not Variant.FAULTY:
            continue
        harmed_family = case_by_id[assignment.case_id].error_family == "family-00"
        if (harmed_family and assignment.condition is Condition.BRAIN_ON) or (
            not harmed_family and assignment.condition is Condition.BRAIN_OFF
        ):
            failing_digests.add(assignment.digest)
    outcomes = tuple(
        _failing_outcome(assignment, authority, case_by_id[assignment.case_id])
        if assignment.digest in failing_digests
        else _passing_outcome(assignment, authority, case_by_id[assignment.case_id])
        for assignment in plan.assignments
    )
    summary = summarize_v2(plan, outcomes, receipt_authority=authority)
    assert summary.mean_case_delta == 0.25
    assert summary.mean_faulty_case_delta == 0.5
    assert summary.strictly_positive_faulty_cases == 30
    assert summary.negative_faulty_cases == 10
    assert summary.worst_faulty_populated_stratum_regression == 1
    assert "faulty_populated_stratum_regression" in summary.failed_gates
    assert "populated_stratum_difficulty_floor" not in summary.failed_gates
    assert "populated_stratum_difficulty_ceiling" not in summary.failed_gates
    assert summary.helped is False


def test_case_cluster_and_faulty_gates_are_achievable_with_independent_effects() -> None:
    cases, _, _, _, authority, _, plan = _public_design(count=40)
    baseline_failures = set()
    effect_cases = set()
    for domain in sorted({case.domain for case in cases}):
        group = sorted(case.case_id for case in cases if case.domain == domain)
        baseline_failures.update(group[:2])
        effect_cases.update(group[2:7])
    outcomes = []
    for assignment in plan.assignments:
        should_fail = assignment.case_id in baseline_failures or (
            assignment.case_id in effect_cases and assignment.condition is Condition.BRAIN_OFF
        )
        outcomes.append(
            _failing_outcome(assignment, authority)
            if should_fail
            else _passing_outcome(assignment, authority)
        )
    summary = summarize_v2(plan, outcomes, receipt_authority=authority)
    assert len(effect_cases) == 20
    assert summary.strictly_positive_cases == 20
    assert summary.strictly_positive_faulty_cases == 20
    assert summary.mean_case_delta == 0.5
    assert summary.mean_faulty_case_delta == 0.5
    assert summary.exact_case_randomization_p == pytest.approx(2**-20)
    assert summary.strictly_positive_pairs == 20
    assert summary.strictly_positive_faulty_pairs == 20
    assert summary.exact_pair_randomization_p == pytest.approx(2**-20)
    assert summary.exact_faulty_pair_randomization_p == pytest.approx(2**-20)
    assert summary.brain_off_success == 0.3
    assert summary.failed_gates == ()
    assert summary.helped is True


def test_ninety_five_percent_brain_off_is_diagnosed_as_ceiling() -> None:
    _, _, _, _, authority, _, plan = _public_design(count=40)
    outcomes = []
    for assignment in plan.assignments:
        # Two brain-off misses in each 40-case pair gives exactly 760 / 800 = 95%.
        should_fail = assignment.condition is Condition.BRAIN_OFF and assignment.order_index < 2
        outcomes.append(
            _failing_outcome(assignment, authority)
            if should_fail
            else _passing_outcome(assignment, authority)
        )
    summary = summarize_v2(plan, outcomes, receipt_authority=authority)
    assert summary.brain_off_success == 0.95
    assert summary.brain_on_success == 1.0
    assert summary.mean_case_delta == 0.05
    assert summary.ceiling.brain_off_errors == 40
    assert summary.ceiling.brain_off_total == 800
    assert summary.ceiling.remaining_headroom == pytest.approx(0.05)
    assert summary.ceiling.benchmark_too_easy is True
    assert "benchmark_ceiling" in summary.failed_gates
    assert summary.helped is False


def test_below_thirty_percent_brain_off_is_diagnosed_as_too_hard() -> None:
    _, _, _, _, authority, _, plan = _public_design(count=40)
    outcomes = []
    for assignment in plan.assignments:
        should_fail = assignment.condition is Condition.BRAIN_OFF and assignment.order_index >= 10
        outcomes.append(
            _failing_outcome(assignment, authority)
            if should_fail
            else _passing_outcome(assignment, authority)
        )
    summary = summarize_v2(plan, outcomes, receipt_authority=authority)
    assert summary.brain_off_success == 0.25
    assert summary.ceiling.benchmark_too_hard is True
    assert summary.ceiling.benchmark_too_easy is False
    assert "benchmark_floor" in summary.failed_gates
    assert summary.helped is False


def test_development_sized_plan_builds_but_cannot_return_helped() -> None:
    _, _, _, _, authority, _, plan = _public_design(count=20)
    outcomes = tuple(_passing_outcome(item, authority) for item in plan.assignments)
    summary = summarize_v2(plan, outcomes, receipt_authority=authority)
    assert summary.case_count == 20
    assert "claim_case_universe" in summary.failed_gates
    assert summary.helped is False


def test_sparse_mechanism_and_domain_strata_fail_closed_and_are_reported() -> None:
    _, _, _, _, authority, _, plan = _public_design(count=40, unique_strata=True)
    outcomes = tuple(_passing_outcome(item, authority) for item in plan.assignments)
    summary = summarize_v2(plan, outcomes, receipt_authority=authority)
    assert summary.adequately_populated_stratum_count == 0
    assert summary.insufficient_stratum_count == 160
    assert summary.minimum_stratum_case_support == 8
    assert "insufficient_stratum_support" in summary.failed_gates
    assert summary.helped is False


def test_public_scalar_boundaries_reject_method_overriding_subclasses() -> None:
    class HostileText(str):
        def encode(self, *_args, **_kwargs):
            raise AssertionError("a rejected string subclass must never control canonical bytes")

    class HostileBytes(bytes):
        def __len__(self) -> int:
            return 64

    class HostileInt(int):
        def __lt__(self, _other) -> bool:
            return False

        def __gt__(self, _other) -> bool:
            return False

    class HostileFloat(float):
        def __ge__(self, _other) -> bool:
            return True

        def __le__(self, _other) -> bool:
            return True

        def __lt__(self, _other) -> bool:
            return False

        def __gt__(self, _other) -> bool:
            return False

    case = _case(0)
    with pytest.raises(ValueError, match="must be a string"):
        replace(case, case_id=HostileText(case.case_id))
    with pytest.raises(ValueError, match="sha256"):
        replace(
            case,
            repair_verifier_digest=HostileText(case.repair_verifier_digest),
        )
    with pytest.raises(ValueError, match="exact bytes"):
        V2ReceiptAuthority(HostileBytes(b"x"))
    with pytest.raises(ValueError, match="integer"):
        V2RenderedView(
            prompt="bounded",
            numbered_solution=("1. line",),
            schema_version=HostileInt(-1),
        )
    with pytest.raises(ValueError, match="finite float"):
        replace(
            V2GateConfig(),
            minimum_mean_case_delta=HostileFloat(float("nan")),
        )


def test_retained_public_collections_require_exact_builtin_types() -> None:
    class HostileDict(dict):
        def __iter__(self):
            raise AssertionError("a rejected dict subclass must never be iterated")

    class HostileList(list):
        def __iter__(self):
            raise AssertionError("a rejected list subclass must never be iterated")

    class HostileTuple(tuple):
        def __iter__(self):
            raise AssertionError("a rejected tuple subclass must never be iterated")

    view_mapping = V2RenderedView(
        prompt="bounded",
        numbered_solution=("1. line",),
    ).to_dict()
    with pytest.raises(ValueError, match="input must be an object"):
        V2RenderedView.from_mapping(HostileDict(view_mapping))
    mapping_with_hostile_lines = dict(view_mapping)
    mapping_with_hostile_lines["numbered_solution"] = HostileList(view_mapping["numbered_solution"])
    with pytest.raises(ValueError, match="must be a sequence"):
        V2RenderedView.from_mapping(mapping_with_hostile_lines)

    *_, plan = _public_design()
    hostile_cases = tuple.__new__(HostileTuple, plan.cases)
    hostile_contexts = tuple.__new__(HostileTuple, plan.contexts)
    with pytest.raises(ValueError, match="exact canonical tuples"):
        replace(plan, cases=hostile_cases)
    with pytest.raises(ValueError, match="exact canonical tuples"):
        replace(plan, contexts=hostile_contexts)
    batch = plan.solver_batch(0, Condition.BRAIN_ON)
    hostile_batch_cases = tuple.__new__(HostileTuple, batch.cases)
    with pytest.raises(ValueError, match="exact records in a tuple"):
        replace(batch, cases=hostile_batch_cases)


def test_consumers_reject_post_construction_list_mutation() -> None:
    (
        cases,
        contexts,
        assignment_seed,
        control_seed,
        authority,
        _,
        plan,
    ) = _public_design()
    object.__setattr__(
        cases[0].clean_view,
        "numbered_solution",
        ["tampered-list"],
    )
    with pytest.raises(ValueError, match="canonical tuple"):
        precommit_v2_design(
            cases,
            contexts,
            assignment_seed=assignment_seed,
            control_seed=control_seed,
            receipt_authority_digest=authority.digest,
        )
    with pytest.raises(ValueError, match="canonical tuple"):
        plan.solver_batch(0, Condition.BRAIN_ON)


def test_consumers_revalidate_mutated_context_and_gate_records() -> None:
    (
        cases,
        contexts,
        assignment_seed,
        control_seed,
        authority,
        _,
        plan,
    ) = _public_design()
    object.__setattr__(contexts[0], "relevance_keys", list(contexts[0].relevance_keys))
    with pytest.raises(ValueError, match="canonical tuple"):
        precommit_v2_design(
            cases,
            contexts,
            assignment_seed=assignment_seed,
            control_seed=control_seed,
            receipt_authority_digest=authority.digest,
        )
    with pytest.raises(ValueError, match="canonical tuple"):
        plan.solver_batch(0, Condition.BRAIN_OFF)

    gate = V2GateConfig()
    cases, contexts, assignment_seed, control_seed, authority, _, plan = _public_design(gate=gate)
    outcomes = tuple(_passing_outcome(item, authority) for item in plan.assignments)
    object.__setattr__(gate, "minimum_mean_case_delta", 0.01)
    with pytest.raises(ValueError, match="cannot be weaker"):
        precommit_v2_design(
            cases,
            contexts,
            assignment_seed=assignment_seed,
            control_seed=control_seed,
            receipt_authority_digest=authority.digest,
            gate=gate,
        )
    with pytest.raises(ValueError, match="cannot be weaker"):
        summarize_v2(plan, outcomes, receipt_authority=authority)


def test_plan_lookup_and_dispatch_reject_mutated_assignment_and_outer_containers() -> None:
    *_, plan = _public_design()
    assignment = next(
        item
        for item in plan.assignments
        if item.condition is Condition.BRAIN_ON and item.variant is Variant.CLEAN
    )
    object.__setattr__(assignment, "variant", Variant.FAULTY)
    with pytest.raises(ValueError, match="assignment state changed after binding"):
        assignment_variant_v2(plan, assignment.case_id, assignment.pair_id)

    class HostileTuple(tuple):
        def __iter__(self):
            raise AssertionError("a rejected tuple subclass must never be iterated")

        def __len__(self):
            raise AssertionError("a rejected tuple subclass must never control length")

    *_, plan = _public_design()
    original_bindings = plan.active_control.bindings
    object.__setattr__(
        plan.active_control,
        "bindings",
        tuple.__new__(HostileTuple, original_bindings),
    )
    with pytest.raises(ValueError, match="active-control bindings must be exact records"):
        plan.solver_batch(0, Condition.BRAIN_ON)

    object.__setattr__(plan.active_control, "bindings", original_bindings)
    object.__setattr__(
        plan,
        "assignments",
        tuple.__new__(HostileTuple, plan.assignments),
    )
    with pytest.raises(ValueError, match="plan assignments must be exact records"):
        plan.solver_batch(0, Condition.BRAIN_ON)


def test_plan_lookup_rejects_cross_plan_cell_transplant_and_duplicate_universe() -> None:
    (
        cases,
        contexts,
        _,
        control_seed,
        authority,
        _,
        plan_a,
    ) = _public_design()
    assignment_seed_b = hashlib.sha256(b"cross-plan-membership-seed").digest()
    commitment_b = precommit_v2_design(
        cases,
        contexts,
        assignment_seed=assignment_seed_b,
        control_seed=control_seed,
        receipt_authority_digest=authority.digest,
    )
    plan_b = build_v2_plan(
        cases,
        contexts,
        assignment_seed=assignment_seed_b,
        control_seed=control_seed,
        commitment=commitment_b,
    )
    by_cell_a = {(item.case_id, item.pair_id, item.condition): item for item in plan_a.assignments}
    by_cell_b = {(item.case_id, item.pair_id, item.condition): item for item in plan_b.assignments}
    transplant_cell = next(
        (case.case_id, pair_id)
        for case in cases
        for pair_id in range(V2_PAIR_COUNT)
        if (
            by_cell_a[(case.case_id, pair_id, Condition.BRAIN_ON)].variant
            is not by_cell_b[(case.case_id, pair_id, Condition.BRAIN_ON)].variant
            and by_cell_a[(case.case_id, pair_id, Condition.BRAIN_ON)].order_index
            == by_cell_b[(case.case_id, pair_id, Condition.BRAIN_ON)].order_index
        )
    )
    transplanted = list(plan_a.assignments)
    for condition in Condition:
        key = (*transplant_cell, condition)
        index = transplanted.index(by_cell_a[key])
        transplanted[index] = by_cell_b[key]
    object.__setattr__(plan_a, "assignments", tuple(transplanted))
    with pytest.raises(ValueError, match="containers differ from their sealed anchor"):
        assignment_variant_v2(plan_a, *transplant_cell)
    with pytest.raises(ValueError, match="containers differ from their sealed anchor"):
        plan_a.assignments_for(transplant_cell[1], Condition.BRAIN_ON)

    *_, plan = _public_design()
    selected = plan.assignments_for(0, Condition.BRAIN_ON)
    assert tuple(item.order_index for item in selected) == tuple(range(len(plan.cases)))
    assert {item.case_id for item in selected} == {case.case_id for case in plan.cases}
    duplicated = list(plan.assignments)
    duplicated[duplicated.index(selected[0])] = selected[1]
    object.__setattr__(plan, "assignments", tuple(duplicated))
    with pytest.raises(ValueError, match="containers differ from their sealed anchor"):
        plan.assignments_for(0, Condition.BRAIN_ON)


def test_plan_lookup_authenticates_returned_case_semantics() -> None:
    *_, plan = _public_design()
    case = plan.cases[0]
    object.__setattr__(case, "domain", f"{case.domain}-changed")

    with pytest.raises(ValueError, match="case record differs from its sealed anchor"):
        plan.assignments_for(0, Condition.BRAIN_ON)
    with pytest.raises(ValueError, match="case record differs from its sealed anchor"):
        assignment_variant_v2(plan, case.case_id, 0)


def test_candidate_json_rejects_subclass_and_nonfinite_semantic_splits() -> None:
    class SplitViewDict(dict):
        def items(self):
            return (("serialized", "benign"),)

    candidates = (
        SplitViewDict({"verifier_reads": "different"}),
        {"score": float("nan")},
        {"tuple_is_not_a_json_array": (1, 2)},
        {1: "object keys are exact strings"},
    )
    for candidate in candidates:
        with pytest.raises(ValueError, match="strict canonical JSON data"):
            solver_answer_digest_v2(
                verdict=Verdict.INVALID,
                root_localization=1,
                error_family="candidate-boundary",
                repair_candidate=candidate,
                witness_candidate=None,
            )


def test_candidate_json_depth_item_integer_and_byte_caps_fail_closed() -> None:
    too_deep: object = 0
    for _ in range(34):
        too_deep = [too_deep]
    oversized_candidates = (
        too_deep,
        list(range(4097)),
        [[0] * 4096 for _ in range(9)],
        1 << 4097,
        ["x" * 220_000] * 5,
    )
    for candidate in oversized_candidates:
        with pytest.raises(ValueError, match="strict canonical JSON data"):
            solver_answer_digest_v2(
                verdict=Verdict.INVALID,
                root_localization=1,
                error_family="candidate-boundary",
                repair_candidate=candidate,
                witness_candidate=None,
            )


def test_v2_case_universe_has_a_hard_upper_bound() -> None:
    with pytest.raises(ValueError, match="may not exceed 512"):
        _public_design(count=513)


def test_canonical_unicode_container_order_and_dos_bounds_fail_closed() -> None:
    with pytest.raises(ValueError, match="canonical tuple"):
        V2RenderedView(prompt="bounded", numbered_solution=["1. line"])  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="canonical NFC Unicode"):
        V2ContextCard(
            case_id="decomposed-e\u0301",
            text="bounded",
            relevance_keys=("domain:x",),
        )
    with pytest.raises(ValueError, match="64 relevance keys"):
        V2ContextCard(
            case_id="too-many-keys",
            text="bounded",
            relevance_keys=tuple(f"key:{index:03d}" for index in range(65)),
        )
    with pytest.raises(ValueError, match="256000-byte budget"):
        V2ContextCard(
            case_id="too-large",
            text="x" * 256_001,
            relevance_keys=("domain:x",),
        )
    with pytest.raises(ValueError, match="case universe may not exceed 512 items"):
        precommit_v2_design(
            repeat(_case(0)),  # type: ignore[arg-type]
            (),
            assignment_seed=hashlib.sha256(b"bounded assignment").digest(),
            control_seed=hashlib.sha256(b"bounded control").digest(),
            receipt_authority_digest=_sha("bounded authority"),
        )
    *_, plan = _public_design()
    with pytest.raises(ValueError, match="canonical pair/condition/order order"):
        replace(plan, assignments=tuple(reversed(plan.assignments)))
    with pytest.raises(ValueError, match="canonical target-case order"):
        V2ActiveControlPlan(
            tuple(reversed(plan.active_control.bindings)),
            plan.active_control.control_seed_commitment,
        )


def test_outcome_iterable_is_bounded_before_duplicate_or_completeness_checks() -> None:
    _, _, _, _, authority, _, plan = _public_design()
    outcome = _passing_outcome(plan.assignments[0], authority)
    with pytest.raises(ValueError, match="outcome universe may not exceed 800 items"):
        summarize_v2(plan, repeat(outcome), receipt_authority=authority)


def test_clean_specificity_and_populated_stratum_regressions_are_hard_gates() -> None:
    cases, _, _, _, authority, _, plan = _public_design(count=40)
    target = cases[0].case_id
    outcomes = []
    for assignment in plan.assignments:
        if assignment.case_id == target:
            should_fail = assignment.condition is Condition.BRAIN_ON
        else:
            should_fail = assignment.order_index < 8
        outcomes.append(
            _failing_outcome(assignment, authority)
            if should_fail
            else _passing_outcome(assignment, authority)
        )
    summary = summarize_v2(plan, outcomes, receipt_authority=authority)
    assert summary.clean_specificity_loss > 0.02
    assert summary.worst_faulty_populated_stratum_regression == 0.1
    assert summary.adequately_populated_stratum_count == 16
    assert summary.insufficient_stratum_count == 0
    assert "clean_specificity_loss" in summary.failed_gates
    assert "faulty_populated_stratum_regression" in summary.failed_gates


def test_summary_is_aggregate_only_and_rejects_incomplete_or_duplicate_data() -> None:
    cases, _, _, _, authority, _, plan = _public_design(count=40)
    outcomes = tuple(_passing_outcome(assignment, authority) for assignment in plan.assignments)
    summary = summarize_v2(plan, outcomes, receipt_authority=authority)
    assert summary.schema_version == 4
    assert summary.design_commitment_digest == plan.commitment.digest
    assert summary.plan_digest == plan.digest
    assert summary.gate_config_digest == plan.gate.digest
    assert summary.result_universe_digest.startswith("sha256:")
    reordered = summarize_v2(
        plan,
        reversed(outcomes),
        receipt_authority=authority,
    )
    assert reordered.result_universe_digest == summary.result_universe_digest
    changed = (_failing_outcome(plan.assignments[0], authority), *outcomes[1:])
    changed_summary = summarize_v2(plan, changed, receipt_authority=authority)
    assert changed_summary.result_universe_digest != summary.result_universe_digest
    encoded = json.dumps(summary.to_dict(), sort_keys=True)
    for case in cases:
        assert case.case_id not in encoded
        assert case.root_localization not in encoded
        assert case.error_family not in encoded
    assert "assignment" not in encoded
    assert "receipt" not in encoded
    assert "pair_deltas" not in encoded
    with pytest.raises(ValueError, match="complete sealed assignment universe"):
        summarize_v2(plan, outcomes[:-1], receipt_authority=authority)
    with pytest.raises(ValueError, match=r"duplicate|may not exceed"):
        summarize_v2(plan, (*outcomes, outcomes[0]), receipt_authority=authority)


def test_summary_revalidates_plan_after_consuming_outcome_iterable() -> None:
    _, _, _, _, authority, _, plan = _public_design(count=40)
    outcomes = tuple(_passing_outcome(assignment, authority) for assignment in plan.assignments)

    class MutatingOutcomes:
        def __iter__(self):
            object.__setattr__(
                plan.cases[0].clean_view,
                "numbered_solution",
                ["post-validation mutation"],
            )
            return iter(outcomes)

    with pytest.raises(ValueError, match="canonical tuple"):
        summarize_v2(
            plan,
            MutatingOutcomes(),
            receipt_authority=authority,
        )


def test_v1_pair_bound_remains_frozen_while_v2_accepts_pair_nineteen() -> None:
    cases, *_, plan = _public_design()
    with pytest.raises(ValueError, match=r"\[0, 9\]"):
        assignment_variant(0, 10)
    assert assignment_variant_v2(plan, cases[0].case_id, 19) in set(Variant)
    with pytest.raises(ValueError, match=r"\[0, 19\]"):
        assignment_variant_v2(plan, cases[0].case_id, 20)


def test_receipts_bind_the_exact_committed_case_not_only_its_id() -> None:
    cases, _, _, _, authority, _, plan = _public_design(count=40)
    assignment = next(item for item in plan.assignments if item.variant is Variant.FAULTY)
    truth = next(case for case in cases if case.case_id == assignment.case_id)
    rogue = replace(
        truth,
        repair_verifier_digest=_sha("rogue-repair"),
        witness_verifier_digest=_sha("rogue-witness"),
    )

    def accept_repair(_candidate):
        return True, _sha("rogue-repair-evidence")

    def accept_witness(_candidate):
        return True, _sha("rogue-witness-evidence")

    outcome = V2Outcome.finalize(
        assignment,
        rogue,
        verdict=Verdict.INVALID,
        root_localization=truth.root_localization,
        error_family=truth.error_family,
        repair_candidate={"repair": 1},
        witness_candidate={"witness": 1},
        receipt_authority=authority,
        repair_verifier=V2BoundVerifier(
            V2VerifierKind.REPAIR,
            rogue.repair_verifier_digest,
            accept_repair,
        ),
        witness_verifier=V2BoundVerifier(
            V2VerifierKind.INDEPENDENT_WITNESS,
            rogue.witness_verifier_digest,
            accept_witness,
        ),
    )
    assert outcome.repair_receipt is not None
    assert outcome.repair_receipt.verifier_digest != truth.repair_verifier_digest
    assert (
        full_case_success_v2(
            assignment,
            truth,
            outcome,
            receipt_authority=authority,
        )
        is False
    )


def test_outcomes_bind_the_exact_context_design_commitment() -> None:
    cases, contexts, assignment_seed, control_seed, authority, _, plan_a = _public_design(count=40)
    changed_contexts = tuple(replace(card, text="X" + card.text[1:]) for card in contexts)
    commitment_b = precommit_v2_design(
        cases,
        changed_contexts,
        assignment_seed=assignment_seed,
        control_seed=control_seed,
        receipt_authority_digest=authority.digest,
    )
    plan_b = build_v2_plan(
        cases,
        changed_contexts,
        assignment_seed=assignment_seed,
        control_seed=control_seed,
        commitment=commitment_b,
    )
    assert plan_a.commitment.digest != plan_b.commitment.digest
    assert {item.digest for item in plan_a.assignments}.isdisjoint(
        item.digest for item in plan_b.assignments
    )
    outcomes_a = tuple(_passing_outcome(assignment, authority) for assignment in plan_a.assignments)
    with pytest.raises(ValueError, match="complete sealed assignment universe"):
        summarize_v2(plan_b, outcomes_a, receipt_authority=authority)


def test_pair_cluster_gate_rejects_three_shared_batch_shocks() -> None:
    _, _, _, _, authority, _, plan = _public_design(count=40)
    shocked_pairs = {0, 2, 12}
    outcomes = tuple(
        _failing_outcome(assignment, authority)
        if assignment.condition is Condition.BRAIN_OFF and assignment.pair_id in shocked_pairs
        else _passing_outcome(assignment, authority)
        for assignment in plan.assignments
    )
    summary = summarize_v2(plan, outcomes, receipt_authority=authority)
    assert summary.mean_case_delta == 0.15
    assert summary.exact_case_randomization_p == pytest.approx(2**-40)
    assert summary.strictly_positive_pairs == 3
    assert summary.exact_pair_randomization_p == 0.125
    assert summary.strictly_positive_faulty_pairs == 3
    assert summary.exact_faulty_pair_randomization_p == 0.125
    assert "strictly_positive_pairs" in summary.failed_gates
    assert "exact_pair_randomization_p" in summary.failed_gates
    assert "pair_interval_lower_bound" in summary.failed_gates
    assert "strictly_positive_faulty_pairs" in summary.failed_gates
    assert "exact_faulty_pair_randomization_p" in summary.failed_gates
    assert "faulty_pair_interval_lower_bound" in summary.failed_gates
    assert summary.helped is False


def test_control_text_must_differ_even_when_length_and_relevance_match() -> None:
    cases = tuple(_case(index, unique_strata=True) for index in range(8))
    contexts = tuple(
        V2ContextCard(case.case_id, "IDENTICAL CARD", case.required_relevance_keys)
        for case in cases
    )
    with pytest.raises(ValueError, match="text-distinct"):
        precommit_v2_design(
            cases,
            contexts,
            assignment_seed=hashlib.sha256(b"identical-control-assignment").digest(),
            control_seed=hashlib.sha256(b"identical-control-map").digest(),
            receipt_authority_digest=_sha("identical-control-authority"),
        )


def test_exact_solver_view_clones_cannot_inflate_case_support() -> None:
    originals = tuple(_case(index, unique_strata=True) for index in range(8))
    clone = replace(
        originals[0],
        case_id="public-case-clone",
        root_localization="clone-root",
    )
    cases = (*originals[:7], clone)
    contexts = tuple(
        V2ContextCard(
            case.case_id,
            f"clone context {index}",
            case.required_relevance_keys,
        )
        for index, case in enumerate(cases)
    )
    with pytest.raises(ValueError, match="solver-view clones"):
        precommit_v2_design(
            cases,
            contexts,
            assignment_seed=hashlib.sha256(b"clone-assignment").digest(),
            control_seed=hashlib.sha256(b"clone-control").digest(),
            receipt_authority_digest=_sha("clone-authority"),
        )


def test_repair_and_witness_cannot_share_one_replay_callback() -> None:
    cases, _, _, _, authority, _, plan = _public_design()
    assignment = next(item for item in plan.assignments if item.variant is Variant.FAULTY)
    case = next(case for case in cases if case.case_id == assignment.case_id)

    def shared_replay(_candidate):
        return True, _sha("shared-replay-evidence")

    with pytest.raises(ValueError, match="distinct replay callbacks"):
        V2Outcome.finalize(
            assignment,
            case,
            verdict=Verdict.INVALID,
            root_localization=case.root_localization,
            error_family=case.error_family,
            repair_candidate={"repair": 1},
            witness_candidate={"witness": 1},
            receipt_authority=authority,
            repair_verifier=V2BoundVerifier(
                V2VerifierKind.REPAIR,
                case.repair_verifier_digest,
                shared_replay,
            ),
            witness_verifier=V2BoundVerifier(
                V2VerifierKind.INDEPENDENT_WITNESS,
                case.witness_verifier_digest,
                shared_replay,
            ),
        )

    repair_candidate = {"repair": 1}
    witness_candidate = {"witness": 1}
    answer_digest = solver_answer_digest_v2(
        verdict=Verdict.INVALID,
        root_localization=case.root_localization,
        error_family=case.error_family,
        repair_candidate=repair_candidate,
        witness_candidate=witness_candidate,
    )
    repair_receipt = authority.issue(
        assignment,
        case,
        kind=V2VerifierKind.REPAIR,
        candidate=repair_candidate,
        solver_answer_digest=answer_digest,
        verifier=V2BoundVerifier(
            V2VerifierKind.REPAIR,
            case.repair_verifier_digest,
            shared_replay,
        ),
    )
    witness_receipt = authority.issue(
        assignment,
        case,
        kind=V2VerifierKind.INDEPENDENT_WITNESS,
        candidate=witness_candidate,
        solver_answer_digest=answer_digest,
        verifier=V2BoundVerifier(
            V2VerifierKind.INDEPENDENT_WITNESS,
            case.witness_verifier_digest,
            shared_replay,
        ),
    )
    assert authority.verifies(repair_receipt)
    assert authority.verifies(witness_receipt)
    assert repair_receipt.replay_origin_digest == witness_receipt.replay_origin_digest
    assembled = V2Outcome(
        assignment_digest=assignment.digest,
        solver_case_id=assignment.solver_case_id,
        verdict=Verdict.INVALID,
        root_localization=case.root_localization,
        error_family=case.error_family,
        repair_candidate=repair_candidate,
        witness_candidate=witness_candidate,
        solver_answer_digest=answer_digest,
        repair_receipt=repair_receipt,
        witness_receipt=witness_receipt,
    )
    assert (
        full_case_success_v2(
            assignment,
            case,
            assembled,
            receipt_authority=authority,
        )
        is False
    )


def test_outcome_universe_has_an_aggregate_candidate_budget() -> None:
    cases, _, _, _, authority, _, plan = _public_design(count=40)
    assignment = next(item for item in plan.assignments if item.variant is Variant.FAULTY)
    case = next(case for case in cases if case.case_id == assignment.case_id)
    large_candidate = {"chunks": ["x" * 200_000] * 4}
    large_outcome = V2Outcome.finalize(
        assignment,
        case,
        verdict=Verdict.INVALID,
        root_localization=case.root_localization,
        error_family=case.error_family,
        repair_candidate=large_candidate,
        witness_candidate=None,
        receipt_authority=authority,
    )
    consumed = 0

    def outcomes():
        nonlocal consumed
        for _ in range(200):
            consumed += 1
            yield large_outcome

    with pytest.raises(ValueError, match="64,000,000-byte budget"):
        summarize_v2(plan, outcomes(), receipt_authority=authority)
    assert consumed < 200


def test_bootstrap_repetition_budget_is_exactly_sealed() -> None:
    assert V2GateConfig().case_bootstrap_repetitions == 4096
    with pytest.raises(ValueError, match="exactly 4,096"):
        V2GateConfig(case_bootstrap_repetitions=4097)
