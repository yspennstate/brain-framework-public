from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import FrozenInstanceError, fields, replace

import pytest

import math_brain
import math_brain.calibration_plan_v3 as plan_v3
from math_brain.calibration_plan_v3 import (
    MAX_MANIFEST_JSON_BYTES,
    MAX_VERIFIER_IMPLEMENTATIONS,
    SEALED_PROFILE_DIGEST,
    AxisCounts,
    CalibrationPlan,
    ManifestRow,
    MaterializedCaseBinding,
    SelectionManifest,
    preregistration_commitment,
    selection_ticket,
    validate_materialization,
)
from math_brain.calibration_v2 import DEFAULT_CONFIG

CALIBRATION_SEED = "sealed-seed-001"


def _digest(label: str) -> str:
    return "sha256:" + hashlib.sha256(label.encode("utf-8")).hexdigest()


def _plan(
    *,
    case_count: int = 288,
    eligible_arities: tuple[int, ...] = (4, 5),
    axis_counts: AxisCounts | None = None,
) -> CalibrationPlan:
    counts = axis_counts or AxisCounts(
        family=4,
        template=4,
        layout=4,
        domain=2,
        fault=2,
        generator_cell=2,
    )
    return CalibrationPlan.build(
        generator_source_digest=_digest("generator source bytes"),
        generator_bundle_digest=_digest("generator source bundle"),
        evaluator_protocol_digest=_digest("evaluator protocol"),
        derivation_algorithm_digest=_digest("counter-mode coordinate derivation v1"),
        derivation_parameters_digest=_digest("coordinate derivation parameters"),
        case_count=case_count,
        axis_counts=counts,
        eligible_arities=eligible_arities,
        position_blind_template_fingerprints=tuple(
            _digest(f"position-blind template {index}") for index in range(counts.template)
        ),
        generator_cell_signatures=tuple(
            _digest(f"generator cell signature {index}") for index in range(counts.generator_cell)
        ),
        verifier_implementation_digests=tuple(
            sorted((_digest("verifier implementation A"), _digest("verifier implementation B")))
        ),
    )


def _corpus(
    plan: CalibrationPlan,
    *,
    seed: str = CALIBRATION_SEED,
) -> tuple[SelectionManifest, tuple[MaterializedCaseBinding, ...]]:
    candidates: list[dict[str, object]] = []
    coordinate_count = (
        plan.axis_counts.family * plan.axis_counts.template * sum(plan.eligible_arities)
    )
    assert plan.case_count % coordinate_count == 0
    repetitions = plan.case_count // coordinate_count
    generator_counter = 0
    for family_index in range(plan.axis_counts.family):
        for template_index in range(plan.axis_counts.template):
            for eligible_arity in plan.eligible_arities:
                for fault_rank in range(eligible_arity):
                    for repetition in range(repetitions):
                        combination = (
                            family_index
                            + 2 * template_index
                            + fault_rank
                            + repetition
                            + eligible_arity
                        ) % (plan.axis_counts.domain * plan.axis_counts.fault)
                        domain_index = combination // plan.axis_counts.fault
                        fault_index = combination % plan.axis_counts.fault
                        cell_index = (
                            family_index + template_index + fault_rank + repetition + eligible_arity
                        ) % plan.axis_counts.generator_cell
                        layout_index = (
                            family_index
                            + 2 * template_index
                            + fault_rank
                            + repetition
                            + eligible_arity
                        ) % plan.axis_counts.layout
                        eligible_lines = tuple(range(2, 2 + eligible_arity))
                        stable_case_id = f"case-{generator_counter:04d}"
                        content_digest = _digest(f"outcome-free content {generator_counter}")
                        program_digest = _digest(f"semantic program {generator_counter}")
                        view_digest = _digest(f"solver view {generator_counter}")
                        ticket = selection_ticket(
                            plan,
                            seed,
                            generator_counter=generator_counter,
                            stable_case_id=stable_case_id,
                            outcome_free_content_digest=content_digest,
                            semantic_program_digest=program_digest,
                            solver_view_digest=view_digest,
                        )
                        candidates.append(
                            {
                                "stable_case_id": stable_case_id,
                                "generator_counter": generator_counter,
                                "outcome_free_content_digest": content_digest,
                                "semantic_program_digest": program_digest,
                                "solver_view_digest": view_digest,
                                "selection_ticket": ticket,
                                "family_index": family_index,
                                "template_index": template_index,
                                "layout_index": layout_index,
                                "domain_index": domain_index,
                                "fault_index": fault_index,
                                "generator_cell_index": cell_index,
                                "eligible_arity": eligible_arity,
                                "fault_rank": fault_rank,
                                "eligible_fault_lines": eligible_lines,
                            }
                        )
                        generator_counter += 1
    assert generator_counter == plan.case_count
    candidates.sort(key=lambda candidate: str(candidate["selection_ticket"]))

    rows: list[ManifestRow] = []
    cases: list[MaterializedCaseBinding] = []
    for case_index, candidate in enumerate(candidates):
        family_index = int(candidate["family_index"])
        template_index = int(candidate["template_index"])
        layout_index = int(candidate["layout_index"])
        domain_index = int(candidate["domain_index"])
        fault_index = int(candidate["fault_index"])
        cell_index = int(candidate["generator_cell_index"])
        eligible_arity = int(candidate["eligible_arity"])
        fault_rank = int(candidate["fault_rank"])
        eligible_lines = tuple(candidate["eligible_fault_lines"])
        case = MaterializedCaseBinding(
            case_index=case_index,
            stable_case_id=str(candidate["stable_case_id"]),
            generator_counter=int(candidate["generator_counter"]),
            outcome_free_content_digest=str(candidate["outcome_free_content_digest"]),
            semantic_program_digest=str(candidate["semantic_program_digest"]),
            solver_view_digest=str(candidate["solver_view_digest"]),
            selection_ticket=str(candidate["selection_ticket"]),
            family_label=f"family-{family_index}",
            template_label=f"template-{template_index}",
            layout_label=f"layout-{layout_index}",
            domain_label=f"domain-{domain_index}",
            fault_label=f"fault-{fault_index}",
            generator_cell_label=f"cell-{cell_index}",
            template_fingerprint=plan.position_blind_template_fingerprints[template_index],
            generator_cell_signature=plan.generator_cell_signatures[cell_index],
            verifier_implementation_digests=plan.verifier_implementation_digests,
            eligible_fault_lines=eligible_lines,
            fault_line=eligible_lines[fault_rank],
        )
        cases.append(case)
        rows.append(
            ManifestRow(
                case_index=case_index,
                coordinate_digest=case.coordinate_digest(),
                stable_case_id=case.stable_case_id,
                generator_counter=case.generator_counter,
                outcome_free_content_digest=case.outcome_free_content_digest,
                semantic_program_digest=case.semantic_program_digest,
                solver_view_digest=case.solver_view_digest,
                selection_ticket=case.selection_ticket,
                family_index=family_index,
                template_index=template_index,
                layout_index=layout_index,
                domain_index=domain_index,
                fault_index=fault_index,
                generator_cell_index=cell_index,
                eligible_arity=eligible_arity,
                fault_rank=fault_rank,
            )
        )
    return SelectionManifest.build(plan, seed, rows), tuple(cases)


def _replace_case_and_rebind_coordinate(
    plan: CalibrationPlan,
    manifest: SelectionManifest,
    cases: tuple[MaterializedCaseBinding, ...],
    case_index: int,
    **changes: object,
) -> tuple[SelectionManifest, tuple[MaterializedCaseBinding, ...]]:
    changed_case = replace(cases[case_index], **changes)
    changed_cases = list(cases)
    changed_cases[case_index] = changed_case
    changed_rows = list(manifest.rows)
    changed_rows[case_index] = replace(
        changed_rows[case_index], coordinate_digest=changed_case.coordinate_digest()
    )
    return SelectionManifest.build(plan, manifest.seed, changed_rows), tuple(changed_cases)


def test_roundtrip_is_exact_canonical_and_immutable() -> None:
    plan = _plan()
    manifest, cases = _corpus(plan)

    assert plan.sealed_profile_digest == SEALED_PROFILE_DIGEST
    assert CalibrationPlan.from_json(plan.canonical_json()) == plan
    assert SelectionManifest.from_json(manifest.canonical_json()) == manifest
    assert CalibrationPlan.from_json(plan.canonical_json()).digest() == plan.digest()
    assert SelectionManifest.from_json(manifest.canonical_json()).digest() == manifest.digest()
    validate_materialization(plan, manifest, cases)

    with pytest.raises(FrozenInstanceError):
        plan.case_count = 201  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        manifest.rows = ()  # type: ignore[misc]


def test_preregistration_types_are_available_from_the_package_api() -> None:
    assert math_brain.AxisCounts is AxisCounts
    assert math_brain.CalibrationPlan is CalibrationPlan
    assert math_brain.ManifestRow is ManifestRow
    assert math_brain.MaterializedCaseBinding is MaterializedCaseBinding
    assert math_brain.SelectionManifest is SelectionManifest
    assert math_brain.preregistration_commitment is preregistration_commitment
    assert math_brain.selection_ticket is selection_ticket
    assert math_brain.validate_materialization is validate_materialization


def test_sealed_profile_digest_matches_live_default_config() -> None:
    assert DEFAULT_CONFIG.digest() == SEALED_PROFILE_DIGEST


def test_every_mirrored_outcome_independent_gate_matches_live_config() -> None:
    mirrors = {
        "MIN_FAMILY_COUNT": "minimum_family_count",
        "MAX_FAMILY_COUNT": "maximum_family_count",
        "MIN_TEMPLATE_COUNT": "minimum_template_count",
        "MAX_TEMPLATE_COUNT": "maximum_template_count",
        "MIN_GENERATOR_CELL_COUNT": "minimum_generator_cell_count",
        "MAX_GENERATOR_CELL_COUNT": "maximum_generator_cell_count",
        "MAX_DOMAIN_FAULT_CELLS": "maximum_domain_fault_cell_count",
        "MIN_GENERATOR_CELL_CASES": "minimum_generator_cell_cases",
        "MIN_GENERATOR_CELL_FAMILIES": "minimum_generator_cell_families",
        "MIN_GENERATOR_CELL_TEMPLATES": "minimum_generator_cell_templates",
        "MIN_GENERATOR_CELL_DOMAINS": "minimum_generator_cell_domains",
        "MIN_GENERATOR_CELL_FAULTS": "minimum_generator_cell_fault_kinds",
        "MIN_DOMAIN_FAULT_CELL_CASES": "minimum_domain_fault_cell_cases",
        "MIN_DOMAIN_FAULT_CELL_FAMILIES": "minimum_domain_fault_cell_families",
        "MIN_DOMAIN_FAULT_CELL_TEMPLATES": "minimum_domain_fault_cell_templates",
        "MIN_FAMILY_CASES": "minimum_family_cases",
        "MIN_FAMILY_TEMPLATES": "minimum_family_templates",
        "MIN_FAMILY_DOMAINS": "minimum_family_domains",
        "MIN_FAMILY_FAULTS": "minimum_family_fault_kinds",
        "MIN_FAMILY_DOMAIN_CASES": "minimum_family_domain_class_cases",
        "MIN_FAMILY_FAULT_CASES": "minimum_family_fault_class_cases",
        "MIN_FAMILY_TEMPLATE_CASES": "minimum_family_template_cases",
        "MIN_FAMILY_TEMPLATE_DOMAINS": "minimum_family_template_domains",
        "MIN_FAMILY_TEMPLATE_FAULTS": "minimum_family_template_fault_kinds",
        "MIN_FAMILY_TEMPLATE_GENERATOR_CELLS": ("minimum_family_template_generator_cells"),
        "MIN_TEMPLATE_CASES": "minimum_template_cases",
        "MIN_TEMPLATE_FAMILIES": "minimum_template_families",
        "MIN_TEMPLATE_DOMAINS": "minimum_template_domains",
        "MIN_TEMPLATE_FAULTS": "minimum_template_fault_kinds",
        "MIN_CASES_PER_ELIGIBLE_RANK": "minimum_cases_per_eligible_rank",
        "MIN_FAULT_POSITION_ENTROPY": "minimum_fault_position_entropy",
        "MAX_FAULT_POSITION_SHARE": "maximum_fault_position_share",
        "MAX_POSITION_UNIFORM_TOTAL_VARIATION": ("maximum_position_uniform_total_variation"),
    }
    for module_name, config_name in mirrors.items():
        assert getattr(plan_v3, module_name) == getattr(DEFAULT_CONFIG, config_name)


def test_any_row_change_after_commitment_is_detected() -> None:
    plan = _plan()
    manifest, cases = _corpus(plan)
    original = preregistration_commitment(plan, manifest, "sealed-seed-001")
    changed_rows = list(manifest.rows)
    first_digest = changed_rows[0].coordinate_digest
    second_digest = changed_rows[1].coordinate_digest
    changed_rows[0] = replace(changed_rows[0], coordinate_digest=second_digest)
    changed_rows[1] = replace(changed_rows[1], coordinate_digest=first_digest)
    changed = SelectionManifest.build(plan, manifest.seed, changed_rows)

    assert preregistration_commitment(plan, changed, "sealed-seed-001") != original
    with pytest.raises(ValueError, match="coordinate digest does not match"):
        validate_materialization(plan, changed, cases)


def test_one_index_aliasing_two_labels_fails_even_when_coordinate_is_rebound() -> None:
    plan = _plan()
    manifest, cases = _corpus(plan)
    changed_manifest, changed_cases = _replace_case_and_rebind_coordinate(
        plan,
        manifest,
        cases,
        4,
        family_label="family-alias",
    )

    with pytest.raises(ValueError, match="family index aliases two labels"):
        validate_materialization(plan, changed_manifest, changed_cases)


def test_one_label_aliasing_two_indices_fails_even_when_all_coordinates_are_rebound() -> None:
    plan = _plan()
    manifest, cases = _corpus(plan)
    changed_rows = list(manifest.rows)
    changed_cases = list(cases)
    for index, (row, case) in enumerate(zip(manifest.rows, cases, strict=True)):
        if row.family_index != 0:
            continue
        changed_case = replace(case, family_label="family-1")
        changed_cases[index] = changed_case
        changed_rows[index] = replace(
            row,
            coordinate_digest=changed_case.coordinate_digest(),
        )
    changed_manifest = SelectionManifest.build(plan, manifest.seed, changed_rows)

    with pytest.raises(ValueError, match="family label aliases two indices"):
        validate_materialization(plan, changed_manifest, changed_cases)


def test_fault_rank_mismatch_fails_directly() -> None:
    plan = _plan()
    manifest, cases = _corpus(plan)
    changed_rows = list(manifest.rows)
    first_index = next(
        index
        for index, row in enumerate(manifest.rows)
        if row.family_index == 0
        and row.template_index == 0
        and row.eligible_arity == 4
        and row.fault_rank == 0
    )
    second_index = next(
        index
        for index, row in enumerate(manifest.rows)
        if row.family_index == 0
        and row.template_index == 0
        and row.eligible_arity == 4
        and row.fault_rank == 1
    )
    first_rank = changed_rows[first_index].fault_rank
    second_rank = changed_rows[second_index].fault_rank
    changed_rows[first_index] = replace(changed_rows[first_index], fault_rank=second_rank)
    changed_rows[second_index] = replace(changed_rows[second_index], fault_rank=first_rank)
    changed = SelectionManifest.build(plan, manifest.seed, changed_rows)

    with pytest.raises(ValueError, match=r"fault line.*eligible_fault_lines\[fault_rank\]"):
        validate_materialization(plan, changed, cases)


def test_noncanonical_json_duplicate_keys_and_non_nfc_are_rejected() -> None:
    plan = _plan()
    manifest, cases = _corpus(plan)
    noncanonical = json.dumps(plan.to_dict(), ensure_ascii=False)
    assert noncanonical != plan.canonical_json()
    with pytest.raises(ValueError, match="exact canonical"):
        CalibrationPlan.from_json(noncanonical)

    needle = f'"plan_digest":"{manifest.plan_digest}"'
    duplicate = manifest.canonical_json().replace(needle, f"{needle},{needle}", 1)
    with pytest.raises(ValueError, match="duplicate JSON object key"):
        SelectionManifest.from_json(duplicate)

    with pytest.raises(ValueError, match="NFC"):
        replace(cases[0], family_label="cafe\u0301")
    with pytest.raises(ValueError, match="NFC"):
        preregistration_commitment(plan, manifest, "se\u0301ed")


def test_relaxed_profile_is_refused_at_construction() -> None:
    plan = _plan()
    with pytest.raises(ValueError, match="relaxed or substituted profile"):
        replace(plan, sealed_profile_digest=_digest("relaxed profile"))


@pytest.mark.parametrize("field", ("generator_source_digest", "generator_bundle_digest"))
def test_generator_source_or_bundle_substitution_breaks_manifest_binding(field: str) -> None:
    plan = _plan()
    manifest, _ = _corpus(plan)
    substituted = replace(plan, **{field: _digest(f"substituted {field}")})

    assert substituted.digest() != plan.digest()
    with pytest.raises(ValueError, match="manifest plan digest"):
        manifest.validate_against(substituted)
    with pytest.raises(ValueError, match="manifest plan digest"):
        preregistration_commitment(substituted, manifest, "sealed-seed-001")


def test_template_digest_substitution_fails_after_coordinate_is_rebound() -> None:
    plan = _plan()
    manifest, cases = _corpus(plan)
    changed_manifest, changed_cases = _replace_case_and_rebind_coordinate(
        plan,
        manifest,
        cases,
        0,
        template_fingerprint=_digest("substituted template implementation"),
    )
    with pytest.raises(ValueError, match="template fingerprint substitution"):
        validate_materialization(plan, changed_manifest, changed_cases)


def test_generator_cell_digest_substitution_fails_after_coordinate_is_rebound() -> None:
    plan = _plan()
    manifest, cases = _corpus(plan)
    changed_manifest, changed_cases = _replace_case_and_rebind_coordinate(
        plan,
        manifest,
        cases,
        0,
        generator_cell_signature=_digest("substituted generator cell"),
    )
    with pytest.raises(ValueError, match="generator-cell signature substitution"):
        validate_materialization(plan, changed_manifest, changed_cases)


def test_verifier_digest_substitution_fails_after_coordinate_is_rebound() -> None:
    plan = _plan()
    manifest, cases = _corpus(plan)
    replacement_verifiers = tuple(
        sorted((plan.verifier_implementation_digests[0], _digest("substituted verifier")))
    )
    changed_manifest, changed_cases = _replace_case_and_rebind_coordinate(
        plan,
        manifest,
        cases,
        0,
        verifier_implementation_digests=replacement_verifiers,
    )
    with pytest.raises(ValueError, match="verifier implementation digest substitution"):
        validate_materialization(plan, changed_manifest, changed_cases)


def test_manifest_requires_complete_contiguous_rows_and_strict_bounds() -> None:
    plan = _plan()
    manifest, _ = _corpus(plan)
    with pytest.raises(ValueError, match="generator counters must be the complete"):
        replace(manifest, rows=manifest.rows[:-1])

    rows = list(manifest.rows)
    rows[0] = replace(rows[0], family_index=plan.axis_counts.family)
    with pytest.raises(ValueError, match=r"family index .* outside plan bounds"):
        SelectionManifest.build(plan, manifest.seed, rows)


def test_bounded_json_and_exact_verifier_multiplicity() -> None:
    plan = _plan()
    with pytest.raises(ValueError, match="byte budget"):
        SelectionManifest.from_json(" " * (MAX_MANIFEST_JSON_BYTES + 1))
    with pytest.raises(ValueError, match="between two"):
        replace(plan, verifier_implementation_digests=(_digest("only verifier"),))


@pytest.mark.parametrize(
    ("field", "label", "maximum"),
    (
        (
            "eligible_arities",
            "eligible arities",
            plan_v3.MAX_ELIGIBLE_ARITY - plan_v3.MIN_ELIGIBLE_ARITY + 1,
        ),
        (
            "position_blind_template_fingerprints",
            "position-blind template fingerprints",
            plan_v3.MAX_TEMPLATE_COUNT,
        ),
        (
            "generator_cell_signatures",
            "generator-cell signatures",
            plan_v3.MAX_GENERATOR_CELL_COUNT,
        ),
        (
            "verifier_implementation_digests",
            "verifier implementation digests",
            MAX_VERIFIER_IMPLEMENTATIONS,
        ),
    ),
)
def test_plan_from_dict_precaps_every_list_before_tuple_copy(
    field: str,
    label: str,
    maximum: int,
) -> None:
    raw = _plan().to_dict()
    values = raw[field]
    assert type(values) is list
    raw[field] = [values[0]] * (maximum + 1)

    with pytest.raises(ValueError, match=rf"{label}.*more than {maximum}"):
        CalibrationPlan.from_dict(raw)


def test_manifest_from_dict_precaps_rows_before_tuple_copy() -> None:
    plan = _plan()
    manifest, _ = _corpus(plan)
    raw = manifest.to_dict()
    raw["rows"] = [raw["rows"][0]] * (plan_v3.MAX_CALIBRATION_CASES + 1)

    with pytest.raises(ValueError, match=r"selection manifest rows.*more than 400"):
        SelectionManifest.from_dict(raw)


def test_materialization_api_has_no_outcome_channel() -> None:
    fields = MaterializedCaseBinding.__dataclass_fields__
    forbidden = {"outcome", "accuracy", "brain_off_correct", "prediction", "answer"}
    assert forbidden.isdisjoint(fields)


def test_live_axis_bounds_and_impossible_support_are_rejected_in_plan() -> None:
    with pytest.raises(ValueError, match="family axis count"):
        replace(_plan().axis_counts, family=3)
    with pytest.raises(ValueError, match="template axis count"):
        replace(_plan().axis_counts, template=17)
    with pytest.raises(ValueError, match="generator-cell axis count"):
        replace(_plan().axis_counts, generator_cell=1)
    with pytest.raises(ValueError, match="domain x fault cells"):
        replace(_plan().axis_counts, domain=3, fault=3)

    with pytest.raises(ValueError, match=r"family support.*requires at least 320"):
        replace(
            _plan(),
            case_count=200,
            axis_counts=replace(_plan().axis_counts, family=8),
        )


def test_balanced_global_ranks_but_fixed_family_template_rank_is_rejected() -> None:
    plan = _plan(case_count=256, eligible_arities=(4,))
    manifest, cases = _corpus(plan)
    changed_rows: list[ManifestRow] = []
    changed_cases: list[MaterializedCaseBinding] = []
    for row, case in zip(manifest.rows, cases, strict=True):
        fixed_rank = (row.family_index + row.template_index) % row.eligible_arity
        changed_case = replace(
            case,
            fault_line=case.eligible_fault_lines[fixed_rank],
        )
        changed_cases.append(changed_case)
        changed_rows.append(
            replace(
                row,
                fault_rank=fixed_rank,
                coordinate_digest=changed_case.coordinate_digest(),
            )
        )
    assert Counter(row.fault_rank for row in changed_rows) == Counter({0: 64, 1: 64, 2: 64, 3: 64})
    changed = replace(manifest, rows=tuple(changed_rows))

    with pytest.raises(ValueError, match=r"family/template .*eligible-rank support"):
        changed.validate_against(plan)
    with pytest.raises(ValueError, match=r"family/template .*eligible-rank support"):
        validate_materialization(plan, changed, changed_cases)


def test_missing_axis_and_undersupported_cells_are_rejected_without_outcomes() -> None:
    plan = _plan()
    manifest, _ = _corpus(plan)

    missing_layout_rows = tuple(
        replace(row, layout_index=2) if row.layout_index == 3 else row for row in manifest.rows
    )
    with pytest.raises(ValueError, match=r"layout axis is incomplete; missing indices=\[3\]"):
        replace(manifest, rows=missing_layout_rows).validate_against(plan)

    cell_one_seen = 0
    thin_cell_rows: list[ManifestRow] = []
    for row in manifest.rows:
        if row.generator_cell_index == 1:
            cell_one_seen += 1
            if cell_one_seen > 39:
                row = replace(row, generator_cell_index=0)
        thin_cell_rows.append(row)
    with pytest.raises(ValueError, match="generator cell index 1 has fewer than 40 rows"):
        replace(manifest, rows=tuple(thin_cell_rows)).validate_against(plan)

    target_seen = 0
    thin_domain_fault_rows: list[ManifestRow] = []
    for row in manifest.rows:
        if row.domain_index == 1 and row.fault_index == 1:
            target_seen += 1
            if target_seen > 39:
                row = replace(row, fault_index=0)
        thin_domain_fault_rows.append(row)
    with pytest.raises(ValueError, match=r"domain/fault .*has fewer than"):
        replace(manifest, rows=tuple(thin_domain_fault_rows)).validate_against(plan)


def test_exact_type_boundaries_reject_all_committed_subclasses_and_raw_str() -> None:
    plan = _plan()
    manifest, cases = _corpus(plan)

    class AxisSubclass(AxisCounts):
        pass

    class PlanSubclass(CalibrationPlan):
        pass

    class RowSubclass(ManifestRow):
        pass

    class ManifestSubclass(SelectionManifest):
        pass

    class BindingSubclass(MaterializedCaseBinding):
        pass

    class RawText(str):
        pass

    def kwargs(value: object) -> dict[str, object]:
        return {field.name: getattr(value, field.name) for field in fields(value)}

    with pytest.raises(ValueError, match="axis counts subclasses"):
        AxisSubclass(**kwargs(plan.axis_counts))
    with pytest.raises(ValueError, match="calibration plan subclasses"):
        PlanSubclass(**kwargs(plan))
    with pytest.raises(ValueError, match="manifest row subclasses"):
        RowSubclass(**kwargs(manifest.rows[0]))
    with pytest.raises(ValueError, match="selection manifest subclasses"):
        ManifestSubclass(**kwargs(manifest))
    with pytest.raises(ValueError, match="binding subclasses"):
        BindingSubclass(**kwargs(cases[0]))
    with pytest.raises(ValueError, match="must be text"):
        CalibrationPlan.from_json(RawText(plan.canonical_json()))


def test_materialized_binding_caps_verifier_multiplicity() -> None:
    plan = _plan()
    _, cases = _corpus(plan)
    too_many = tuple(sorted(_digest(f"verifier-{index}") for index in range(17)))
    assert len(too_many) == MAX_VERIFIER_IMPLEMENTATIONS + 1
    with pytest.raises(ValueError, match="between two and 16"):
        replace(cases[0], verifier_implementation_digests=too_many)


def test_hostile_subclasses_cannot_override_commitment_or_capability_methods() -> None:
    plan = _plan()
    manifest, cases = _corpus(plan)

    def kwargs(value: object) -> dict[str, object]:
        return {field.name: getattr(value, field.name) for field in fields(value)}

    class HostilePlan(CalibrationPlan):
        def __post_init__(self) -> None:
            pass

        def digest(self) -> str:
            return plan.digest()

    hostile_plan_values = kwargs(plan)
    hostile_plan_values["generator_source_digest"] = _digest("substituted source")
    hostile_plan = HostilePlan(**hostile_plan_values)
    with pytest.raises(ValueError, match="exact CalibrationPlan"):
        manifest.validate_against(hostile_plan)
    with pytest.raises(ValueError, match="exact CalibrationPlan"):
        preregistration_commitment(hostile_plan, manifest, manifest.seed)

    class HostileRow(ManifestRow):
        def __post_init__(self) -> None:
            pass

        def to_dict(self) -> dict[str, object]:
            return manifest.rows[0].to_dict()

    hostile_row_values = kwargs(manifest.rows[0])
    hostile_row_values["fault_rank"] = 1
    hostile_row = HostileRow(**hostile_row_values)
    with pytest.raises(ValueError, match="ManifestRow values"):
        replace(manifest, rows=(hostile_row, *manifest.rows[1:]))

    class HostileBinding(MaterializedCaseBinding):
        def __post_init__(self) -> None:
            pass

        def coordinate_digest(self) -> str:
            return manifest.rows[0].coordinate_digest

    hostile_binding = HostileBinding(**kwargs(cases[0]))
    object.__setattr__(hostile_binding, "outcome", True)
    with pytest.raises(ValueError, match="exact MaterializedCaseBinding"):
        validate_materialization(plan, manifest, (hostile_binding, *cases[1:]))

    class HostileManifest(SelectionManifest):
        def __post_init__(self) -> None:
            pass

        def validate_against(self, candidate: CalibrationPlan) -> None:
            del candidate

    hostile_manifest = HostileManifest(**kwargs(manifest))
    with pytest.raises(ValueError, match="exact SelectionManifest"):
        preregistration_commitment(plan, hostile_manifest, manifest.seed)
    with pytest.raises(ValueError, match="exact SelectionManifest"):
        validate_materialization(plan, hostile_manifest, cases)


def test_consumers_revalidate_exact_records_after_object_setattr_tampering() -> None:
    plan = _plan()
    manifest, cases = _corpus(plan)

    axis_tampered_plan = CalibrationPlan.from_json(plan.canonical_json())
    object.__setattr__(axis_tampered_plan.axis_counts, "family", True)
    with pytest.raises(ValueError, match="family axis count must be an integer"):
        manifest.validate_against(axis_tampered_plan)

    tuple_tampered_plan = CalibrationPlan.from_json(plan.canonical_json())
    object.__setattr__(tuple_tampered_plan, "eligible_arities", list(plan.eligible_arities))
    first_row = manifest.rows[0]
    with pytest.raises(ValueError, match="eligible arities must be a nonempty tuple"):
        selection_ticket(
            tuple_tampered_plan,
            manifest.seed,
            generator_counter=first_row.generator_counter,
            stable_case_id=first_row.stable_case_id,
            outcome_free_content_digest=first_row.outcome_free_content_digest,
            semantic_program_digest=first_row.semantic_program_digest,
            solver_view_digest=first_row.solver_view_digest,
        )
    with pytest.raises(ValueError, match="eligible arities must be a nonempty tuple"):
        preregistration_commitment(tuple_tampered_plan, manifest, manifest.seed)

    tuple_tampered_manifest = SelectionManifest.from_json(manifest.canonical_json())
    object.__setattr__(tuple_tampered_manifest, "rows", list(tuple_tampered_manifest.rows))
    with pytest.raises(ValueError, match="manifest rows must be a tuple"):
        preregistration_commitment(plan, tuple_tampered_manifest, manifest.seed)

    row_tampered_manifest = SelectionManifest.from_json(manifest.canonical_json())
    object.__setattr__(row_tampered_manifest.rows[0], "family_index", True)
    with pytest.raises(ValueError, match="family index must be an integer"):
        row_tampered_manifest.validate_against(plan)

    tuple_tampered_binding = replace(cases[0])
    object.__setattr__(
        tuple_tampered_binding,
        "eligible_fault_lines",
        list(tuple_tampered_binding.eligible_fault_lines),
    )
    with pytest.raises(ValueError, match="eligible fault lines must be a tuple"):
        validate_materialization(plan, manifest, (tuple_tampered_binding, *cases[1:]))

    bool_tampered_binding = replace(cases[0])
    object.__setattr__(bool_tampered_binding, "case_index", True)
    with pytest.raises(ValueError, match="case index must be an integer"):
        validate_materialization(plan, manifest, (bool_tampered_binding, *cases[1:]))


def test_manifest_revalidation_rejects_reversed_tickets_and_recomputed_duplicate_id() -> None:
    plan = _plan()
    manifest, _ = _corpus(plan)

    reversed_tickets = SelectionManifest.from_json(manifest.canonical_json())
    first_ticket = reversed_tickets.rows[0].selection_ticket
    second_ticket = reversed_tickets.rows[1].selection_ticket
    object.__setattr__(reversed_tickets.rows[0], "selection_ticket", second_ticket)
    object.__setattr__(reversed_tickets.rows[1], "selection_ticket", first_ticket)
    with pytest.raises(ValueError, match="ordered by selection ticket"):
        reversed_tickets.validate_against(plan)

    duplicate_id = SelectionManifest.from_json(manifest.canonical_json())
    first, second = duplicate_id.rows[:2]
    object.__setattr__(second, "stable_case_id", first.stable_case_id)
    object.__setattr__(
        second,
        "selection_ticket",
        selection_ticket(
            plan,
            duplicate_id.seed,
            generator_counter=second.generator_counter,
            stable_case_id=second.stable_case_id,
            outcome_free_content_digest=second.outcome_free_content_digest,
            semantic_program_digest=second.semantic_program_digest,
            solver_view_digest=second.solver_view_digest,
        ),
    )
    with pytest.raises(ValueError, match="stable case ids must be unique"):
        duplicate_id.validate_against(plan)


def test_plan_feasibility_uses_complete_family_template_product_and_all_ranks() -> None:
    counts = AxisCounts(
        family=4,
        template=5,
        layout=4,
        domain=2,
        fault=2,
        generator_cell=2,
    )
    with pytest.raises(ValueError, match=r"complete family x template support.*at least 240"):
        _plan(case_count=200, eligible_arities=(4,), axis_counts=counts)
    with pytest.raises(ValueError, match=r"complete family x template support.*at least 288"):
        _plan(case_count=287, eligible_arities=(4, 5))


def test_plan_feasibility_solves_discrete_family_template_position_constraints() -> None:
    with pytest.raises(ValueError, match="discrete family x template fault-position"):
        _plan(case_count=201, eligible_arities=(4,))
    assert _plan(case_count=204, eligible_arities=(4,)).case_count == 204

    feasible = _plan(case_count=201, eligible_arities=(5,))
    assert feasible.case_count == 201

    with pytest.raises(ValueError, match="discrete family x template fault-position"):
        _plan(case_count=289, eligible_arities=(4, 5))
    assert _plan(case_count=290, eligible_arities=(4, 5)).case_count == 290


def test_missing_declared_family_template_pair_is_rejected() -> None:
    counts = AxisCounts(
        family=4,
        template=5,
        layout=4,
        domain=2,
        fault=2,
        generator_cell=2,
    )
    plan = _plan(case_count=360, axis_counts=counts)
    manifest, _ = _corpus(plan)
    sparse = tuple(
        replace(row, template_index=0) if row.family_index == 0 and row.template_index == 4 else row
        for row in manifest.rows
    )
    with pytest.raises(ValueError, match=r"family/template \(0, 4\) has fewer than 12"):
        replace(manifest, rows=sparse).validate_against(plan)


def test_each_family_template_requires_every_declared_arity() -> None:
    plan = _plan()
    manifest, _ = _corpus(plan)
    changed: list[ManifestRow] = []
    offset = 0
    for row in manifest.rows:
        if row.family_index == 0 and row.template_index == 0:
            row = replace(row, eligible_arity=4, fault_rank=offset % 4)
            offset += 1
        changed.append(row)
    with pytest.raises(ValueError, match=r"family/template \(0, 0\) omits declared"):
        replace(manifest, rows=tuple(changed)).validate_against(plan)


def test_every_family_requires_each_domain_fault_joint_cell() -> None:
    plan = _plan(case_count=256, eligible_arities=(4,))
    manifest, _ = _corpus(plan)
    family_offsets = {family: 0 for family in range(4)}
    changed: list[ManifestRow] = []
    for row in manifest.rows:
        offset = family_offsets[row.family_index]
        family_offsets[row.family_index] += 1
        domain = offset % 2
        fault = domain if row.family_index < 2 else 1 - domain
        changed.append(replace(row, domain_index=domain, fault_index=fault))
    with pytest.raises(ValueError, match=r"family/domain/fault \(0, 0, 1\)"):
        replace(manifest, rows=tuple(changed)).validate_against(plan)


@pytest.mark.parametrize(
    ("dimension", "selector"),
    (
        ("families", lambda row: row.family_index == 0),
        ("templates", lambda row: row.template_index < 3),
        ("domains", lambda row: row.domain_index == 0),
        ("faults", lambda row: row.fault_index == 0),
    ),
)
def test_generator_cell_mirrors_every_sealed_diversity_gate(
    dimension: str,
    selector: object,
) -> None:
    plan = _plan()
    manifest, _ = _corpus(plan)
    changed = tuple(
        replace(row, generator_cell_index=1 if selector(row) else 0)  # type: ignore[operator]
        for row in manifest.rows
    )
    with pytest.raises(ValueError, match=rf"generator cell index .*distinct {dimension}"):
        replace(manifest, rows=changed).validate_against(plan)


def test_domain_fault_cell_requires_four_templates() -> None:
    plan = _plan()
    manifest, _ = _corpus(plan)
    changed = tuple(
        replace(row, fault_index=1)
        if row.domain_index == 0 and row.fault_index == 0 and row.template_index == 3
        else row
        for row in manifest.rows
    )
    with pytest.raises(ValueError, match=r"domain/fault \(0, 0\).*distinct templates"):
        replace(manifest, rows=changed).validate_against(plan)


def test_generator_cells_cannot_partition_declared_arities() -> None:
    plan = _plan()
    manifest, _ = _corpus(plan)
    changed = tuple(
        replace(row, generator_cell_index=0 if row.eligible_arity == 4 else 1)
        for row in manifest.rows
    )

    with pytest.raises(ValueError, match=r"generator cell index .*omits declared eligible arities"):
        replace(manifest, rows=changed).validate_against(plan)


def test_domain_fault_cells_cannot_partition_declared_arities() -> None:
    plan = _plan()
    manifest, _ = _corpus(plan)
    changed = tuple(
        replace(row, fault_index=0 if row.eligible_arity == 4 else 1) for row in manifest.rows
    )

    with pytest.raises(
        ValueError,
        match=r"domain/fault \(0, 0\) omits declared eligible arities",
    ):
        replace(manifest, rows=changed).validate_against(plan)


@pytest.mark.parametrize(
    ("dimension", "changes"),
    (
        ("domains", {"domain_index": 0}),
        ("faults", {"fault_index": 0}),
        ("generator cells", {"generator_cell_index": 0}),
    ),
)
def test_family_template_mirrors_every_sealed_diversity_gate(
    dimension: str,
    changes: dict[str, int],
) -> None:
    plan = _plan()
    manifest, _ = _corpus(plan)
    changed = tuple(
        replace(row, **changes) if row.family_index == 0 and row.template_index == 0 else row
        for row in manifest.rows
    )
    with pytest.raises(ValueError, match=rf"family/template \(0, 0\).*distinct {dimension}"):
        replace(manifest, rows=changed).validate_against(plan)


def test_position_balance_gates_reject_supported_but_concentrated_ranks() -> None:
    plan = _plan(case_count=320, eligible_arities=(4,))
    manifest, _ = _corpus(plan)
    offsets: dict[tuple[int, int], int] = {}
    changed: list[ManifestRow] = []
    rank_schedule = (0,) * 14 + (1,) * 2 + (2,) * 2 + (3,) * 2
    for row in manifest.rows:
        key = (row.family_index, row.template_index)
        offset = offsets.get(key, 0)
        offsets[key] = offset + 1
        changed.append(replace(row, fault_rank=rank_schedule[offset]))
    with pytest.raises(
        ValueError,
        match=r"fault-position entropy|maximum fault-position share|total variation",
    ):
        replace(manifest, rows=tuple(changed)).validate_against(plan)


@pytest.mark.parametrize(
    "field",
    (
        "stable_case_id",
        "outcome_free_content_digest",
        "semantic_program_digest",
        "solver_view_digest",
    ),
)
def test_case_identity_and_every_outcome_free_digest_must_be_unique(field: str) -> None:
    plan = _plan()
    manifest, _ = _corpus(plan)
    rows = list(manifest.rows)
    rows[1] = replace(rows[1], **{field: getattr(rows[0], field)})
    with pytest.raises(ValueError, match="must be unique"):
        replace(manifest, rows=tuple(rows))


def test_content_swap_is_detected_by_seed_ticket() -> None:
    plan = _plan()
    manifest, _ = _corpus(plan)
    rows = list(manifest.rows)
    first = rows[0].outcome_free_content_digest
    second = rows[1].outcome_free_content_digest
    rows[0] = replace(rows[0], outcome_free_content_digest=second)
    rows[1] = replace(rows[1], outcome_free_content_digest=first)
    changed = replace(manifest, rows=tuple(rows))
    with pytest.raises(ValueError, match="selection ticket does not match"):
        changed.validate_against(plan)


def test_indices_and_fault_lines_reject_values_outside_bounded_domains() -> None:
    plan = _plan()
    manifest, cases = _corpus(plan)
    huge = 10**10_000

    with pytest.raises(ValueError, match="generator counter is outside the exact plan range"):
        selection_ticket(
            plan,
            manifest.seed,
            generator_counter=huge,
            stable_case_id=manifest.rows[0].stable_case_id,
            outcome_free_content_digest=manifest.rows[0].outcome_free_content_digest,
            semantic_program_digest=manifest.rows[0].semantic_program_digest,
            solver_view_digest=manifest.rows[0].solver_view_digest,
        )

    raw_row = manifest.rows[0].to_dict()
    raw_row["family_index"] = huge
    with pytest.raises(ValueError, match="family index must be smaller than 400"):
        ManifestRow.from_dict(raw_row)

    with pytest.raises(ValueError, match=r"eligible fault lines.*supported solver trace"):
        replace(
            cases[0],
            eligible_fault_lines=(2, 3, 4, huge),
            fault_line=2,
        )
    with pytest.raises(ValueError, match=r"fault line.*supported solver trace"):
        replace(cases[0], fault_line=huge)


def test_materialized_content_swap_is_detected_before_coordinate_acceptance() -> None:
    plan = _plan()
    manifest, cases = _corpus(plan)
    changed = list(cases)
    changed[0] = replace(
        changed[0],
        outcome_free_content_digest=cases[1].outcome_free_content_digest,
    )
    with pytest.raises(ValueError, match="outcome free content digest does not match"):
        validate_materialization(plan, manifest, changed)


def test_seed_tickets_and_canonical_order_reject_seed_row_and_ticket_reordering() -> None:
    plan = _plan()
    manifest, _ = _corpus(plan)
    with pytest.raises(ValueError, match="selection ticket does not match"):
        replace(manifest, seed="different-seed").validate_against(plan)
    with pytest.raises(ValueError, match="commitment seed does not match"):
        preregistration_commitment(plan, manifest, "different-seed")

    rows = list(manifest.rows)
    rows[0], rows[1] = replace(rows[1], case_index=0), replace(rows[0], case_index=1)
    with pytest.raises(ValueError, match="ordered by selection ticket"):
        replace(manifest, rows=tuple(rows))

    rows = list(manifest.rows)
    rows[0] = replace(rows[0], generator_counter=rows[1].generator_counter)
    rows[1] = replace(rows[1], generator_counter=manifest.rows[0].generator_counter)
    changed = replace(manifest, rows=tuple(rows))
    with pytest.raises(ValueError, match="selection ticket does not match"):
        changed.validate_against(plan)
