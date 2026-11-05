from __future__ import annotations

import copy
import hashlib
import inspect
import re
from dataclasses import replace
from fractions import Fraction

import pytest

import math_brain.relational_leakage_v3 as relational_module
from math_brain.hard_challenge_v2 import generate_public_calibration_bank_v2
from math_brain.relational_leakage_v3 import (
    DEFAULT_MAXIMUM_FAULT_LINE_ACCURACY_V3,
    DEFAULT_MAXIMUM_MECHANISM_ACCURACY_V3,
    RELATIONAL_LEAKAGE_SCHEMA_V3,
    RelationalGroupManifestEntryV3,
    RelationalGroupManifestV3,
    RelationalLeakageExampleV3,
    RelationalSolverFeaturesV3,
    RelationalSolverViewV3,
    build_relational_group_manifest_v3,
    evaluate_grouped_relational_leakage_v3,
    extract_relational_solver_features_v3,
    relational_leakage_input_digest_v3,
)


def _digest(label: str) -> str:
    return "sha256:" + hashlib.sha256(label.encode()).hexdigest()


def _hard_v2_examples() -> tuple[RelationalLeakageExampleV3, ...]:
    return tuple(
        RelationalLeakageExampleV3(
            group=f"numeric-instance-{case.seed // 512:02d}",
            group_binding_digest=_digest(f"hard-v2-instance:{case.seed // 512}"),
            case_binding_digest=case.digest(),
            family=str(case.family),
            mechanism=str(case.mechanism),
            fault_line=case.faulty_line,
            solver_view=RelationalSolverViewV3(
                prompt=case.prompt,
                candidate_lines=tuple(enumerate(case.trace(faulty=True)[2:10], 3)),
            ),
        )
        for case in generate_public_calibration_bank_v2()
    )


def _evaluate(
    examples: tuple[RelationalLeakageExampleV3, ...] | list[RelationalLeakageExampleV3],
):
    values = tuple(examples)
    group_manifest = _group_manifest(values)
    return evaluate_grouped_relational_leakage_v3(
        values,
        group_manifest=group_manifest,
        expected_group_manifest_digest=group_manifest.content_digest,
        expected_input_digest=relational_leakage_input_digest_v3(
            values,
            group_manifest=group_manifest,
        ),
    )


def _group_manifest(
    examples: tuple[RelationalLeakageExampleV3, ...]
    | list[RelationalLeakageExampleV3],
) -> RelationalGroupManifestV3:
    groups: dict[str, tuple[str, list[tuple[str, str]]]] = {}
    for example in examples:
        binding, cases = groups.setdefault(
            example.group,
            (example.group_binding_digest, []),
        )
        if binding != example.group_binding_digest:
            raise ValueError("test fixture changes a group binding")
        cases.append((example.case_binding_digest, example.solver_view.content_digest))
    return build_relational_group_manifest_v3(
        RelationalGroupManifestEntryV3(
            group=group,
            group_binding_digest=binding,
            case_binding_digests=tuple(case for case, _ in sorted(cases)),
            solver_view_digests=tuple(view for _, view in sorted(cases)),
        )
        for group, (binding, cases) in groups.items()
    )


def test_grouped_relational_audit_reproduces_the_hard_v2_release_blocker() -> None:
    report = _evaluate(_hard_v2_examples())

    assert report.total_cases == 320
    assert report.fault_line_correct == report.mechanism_correct == 320
    assert report.fault_line_accuracy == report.mechanism_accuracy == 1.0
    assert report.worst_group_fault_line_accuracy == 1.0
    assert report.worst_group_mechanism_accuracy == 1.0
    assert report.maximum_fault_line_accuracy == DEFAULT_MAXIMUM_FAULT_LINE_ACCURACY_V3
    assert report.maximum_mechanism_accuracy == DEFAULT_MAXIMUM_MECHANISM_ACCURACY_V3
    assert report.group_manifest_digest == (
        "sha256:048eca72f2aa550d5f7e6aa7d14a02f56a99a2da5bd818c29d15a292004bc36e"
    )
    assert report.input_digest == (
        "sha256:d749af3519ab17a2a7d4456d417313dc4e72b5e30d6d47871d30ea5428d80b1a"
    )
    assert report.solver_feature_digest == (
        "sha256:e610b668033952bf1c04358eabaa069ef09cf03be580b7be19633e823325d619"
    )
    assert report.release_gate_passed is False
    assert len(report.folds) == 20
    assert {fold.total_cases for fold in report.folds} == {16}
    assert {fold.fault_line_accuracy for fold in report.folds} == {1.0}
    assert {fold.mechanism_accuracy for fold in report.folds} == {1.0}
    document = report.to_dict()
    assert document["schema"] == RELATIONAL_LEAKAGE_SCHEMA_V3
    assert re.fullmatch(r"sha256:[0-9a-f]{64}", document["input_digest"])
    assert document["split"] == "leave-generator-group-out-v1"
    assert document["release_gate_passed"] is False


def test_relational_audit_is_invariant_to_solver_surface_alias_names() -> None:
    def rotate_example(example: RelationalLeakageExampleV3) -> RelationalLeakageExampleV3:
        aliases = sorted(set(re.findall(r"v-[0-9]{2}", example.prompt)))
        replacements = {
            alias: aliases[(index + 1) % len(aliases)] for index, alias in enumerate(aliases)
        }

        def rotate_aliases(text: str) -> str:
            return re.sub(r"v-[0-9]{2}", lambda match: replacements[match.group()], text)

        return replace(
            example,
            case_binding_digest=_digest(f"renamed:{example.case_binding_digest}"),
            solver_view=RelationalSolverViewV3(
                prompt=rotate_aliases(example.prompt),
                candidate_lines=tuple(
                    (line_number, rotate_aliases(line))
                    for line_number, line in example.candidate_lines
                ),
            ),
        )

    renamed = tuple(rotate_example(example) for example in _hard_v2_examples())
    report = _evaluate(renamed)

    assert report.fault_line_accuracy == 1.0
    assert report.mechanism_accuracy == 1.0


def test_relational_audit_rejects_an_unlegendized_solver_alias() -> None:
    examples = list(_hard_v2_examples())
    first = examples[0]
    line_number, line = first.candidate_lines[0]
    examples[0] = replace(
        first,
        solver_view=RelationalSolverViewV3(
            prompt=first.prompt,
            candidate_lines=(
                (line_number, line.replace("; basis ", "; basis v-99+", 1)),
                *first.candidate_lines[1:],
            ),
        ),
    )

    with pytest.raises(ValueError, match="alias missing from the public legend"):
        _evaluate(examples)


def test_relational_audit_rejects_cloned_views_and_counterfeit_groups() -> None:
    examples = list(_hard_v2_examples())
    first = examples[0]
    clone = replace(
        first,
        group="counterfeit-group",
        group_binding_digest=_digest("counterfeit-group"),
        case_binding_digest=_digest("counterfeit-case"),
    )
    with pytest.raises(ValueError, match="solver views must be globally unique"):
        _evaluate((*examples, clone))

    examples[1] = replace(
        examples[1],
        group=first.group,
    )
    with pytest.raises(ValueError, match=r"group binding|form a bijection"):
        _evaluate(examples)


def test_relational_release_thresholds_cannot_be_relaxed() -> None:
    report = _evaluate(_hard_v2_examples())
    with pytest.raises(ValueError, match="fixed and cannot be relaxed"):
        replace(report, maximum_fault_line_accuracy=1.0, maximum_mechanism_accuracy=1.0)

    object.__setattr__(report.folds[0], "fault_line_correct", 0)
    with pytest.raises(ValueError, match="do not reproduce"):
        report.to_dict()


def test_relational_evaluation_requires_the_preregistered_input_manifest() -> None:
    examples = list(_hard_v2_examples())
    group_manifest = _group_manifest(examples)
    commitment = relational_leakage_input_digest_v3(
        examples,
        group_manifest=group_manifest,
    )

    examples[0] = replace(examples[0], mechanism="counterfeit-mechanism")
    with pytest.raises(ValueError, match=r"committed (?:group )?manifest"):
        evaluate_grouped_relational_leakage_v3(
            examples,
            group_manifest=group_manifest,
            expected_group_manifest_digest=group_manifest.content_digest,
            expected_input_digest=commitment,
        )

    examples = list(_hard_v2_examples())
    examples[0] = replace(
        examples[0],
        group="counterfeit-group",
        group_binding_digest=_digest("counterfeit-group"),
    )
    with pytest.raises(ValueError, match="committed group manifest"):
        evaluate_grouped_relational_leakage_v3(
            examples,
            group_manifest=group_manifest,
            expected_group_manifest_digest=group_manifest.content_digest,
            expected_input_digest=commitment,
        )


def test_group_manifest_is_canonical_round_trippable_and_report_bound() -> None:
    examples = _public_canary_examples("coefficient")
    manifest = _group_manifest(examples)

    assert RelationalGroupManifestV3.from_dict(manifest.to_dict()) == manifest
    assert tuple(entry.group for entry in manifest.entries) == tuple(
        sorted(entry.group for entry in manifest.entries)
    )
    assert tuple(inspect.signature(build_relational_group_manifest_v3).parameters) == (
        "entries",
    )
    assert not {
        "family",
        "fault_line",
        "mechanism",
        "solver_view",
    } & set(inspect.signature(build_relational_group_manifest_v3).parameters)

    report = _evaluate(examples)
    assert report.group_manifest_digest == manifest.content_digest
    assert report.to_dict()["group_manifest_digest"] == manifest.content_digest


def test_external_group_commitment_is_checked_before_examples_are_consumed() -> None:
    examples = _public_canary_examples("coefficient")
    manifest = _group_manifest(examples)

    class ExplodingExamples:
        def __iter__(self):
            raise AssertionError("examples were consumed before external group authority")

    with pytest.raises(ValueError, match="external commitment"):
        evaluate_grouped_relational_leakage_v3(
            ExplodingExamples(),  # type: ignore[arg-type]
            group_manifest=manifest,
            expected_group_manifest_digest=_digest("wrong-group-manifest"),
            expected_input_digest=_digest("unreachable-input"),
        )


def test_fixed_group_manifest_rejects_regrouping_and_solver_view_transplant() -> None:
    examples = list(_public_canary_examples("coefficient"))
    manifest = _group_manifest(examples)

    regrouped = list(examples)
    regrouped[0] = replace(
        regrouped[0],
        group=regrouped[-1].group,
        group_binding_digest=regrouped[-1].group_binding_digest,
    )
    malicious_input = relational_module._input_digest(
        regrouped,
        group_manifest_digest=manifest.content_digest,
    )
    with pytest.raises(ValueError, match="committed group manifest"):
        evaluate_grouped_relational_leakage_v3(
            regrouped,
            group_manifest=manifest,
            expected_group_manifest_digest=manifest.content_digest,
            expected_input_digest=malicious_input,
        )

    transplanted = list(examples)
    transplanted[0] = replace(
        transplanted[0],
        solver_view=transplanted[1].solver_view,
    )
    malicious_input = relational_module._input_digest(
        transplanted,
        group_manifest_digest=manifest.content_digest,
    )
    with pytest.raises(ValueError, match="committed group manifest"):
        evaluate_grouped_relational_leakage_v3(
            transplanted,
            group_manifest=manifest,
            expected_group_manifest_digest=manifest.content_digest,
            expected_input_digest=malicious_input,
        )


def test_group_manifest_rejects_nested_tamper_duplicate_views_and_reordering() -> None:
    examples = _public_canary_examples("coefficient")
    manifest = _group_manifest(examples)

    tampered = copy.deepcopy(manifest)
    object.__setattr__(
        tampered.entries[0],
        "solver_view_digests",
        (*tampered.entries[0].solver_view_digests[:-1], _digest("tampered-view")),
    )
    with pytest.raises(ValueError, match="group-entry digest"):
        tampered.validate_current()

    first, second = manifest.entries
    duplicate_view = replace(
        second,
        solver_view_digests=(first.solver_view_digests[0], *second.solver_view_digests[1:]),
    )
    with pytest.raises(ValueError, match="solver views must be globally unique"):
        RelationalGroupManifestV3(entries=(first, duplicate_view))

    with pytest.raises(ValueError, match="entries must be canonical"):
        RelationalGroupManifestV3(entries=tuple(reversed(manifest.entries)))

    class EntrySubclass(RelationalGroupManifestEntryV3):
        pass

    with pytest.raises(ValueError, match="subclasses are forbidden"):
        EntrySubclass(
            group=first.group,
            group_binding_digest=first.group_binding_digest,
            case_binding_digests=first.case_binding_digests,
            solver_view_digests=first.solver_view_digests,
        )

    first_pairs = list(
        zip(first.case_binding_digests, first.solver_view_digests, strict=True)
    )
    second_pairs = list(
        zip(second.case_binding_digests, second.solver_view_digests, strict=True)
    )
    first_pairs[0], second_pairs[0] = second_pairs[0], first_pairs[0]

    def rebuilt_entry(
        entry: RelationalGroupManifestEntryV3,
        pairs: list[tuple[str, str]],
    ) -> RelationalGroupManifestEntryV3:
        ordered = sorted(pairs)
        return RelationalGroupManifestEntryV3(
            group=entry.group,
            group_binding_digest=entry.group_binding_digest,
            case_binding_digests=tuple(case for case, _ in ordered),
            solver_view_digests=tuple(view for _, view in ordered),
        )

    other_manifest = build_relational_group_manifest_v3(
        (rebuilt_entry(first, first_pairs), rebuilt_entry(second, second_pairs))
    )
    original_input = relational_module._input_digest(
        examples,
        group_manifest_digest=manifest.content_digest,
    )
    other_input = relational_module._input_digest(
        examples,
        group_manifest_digest=other_manifest.content_digest,
    )
    assert other_manifest.content_digest != manifest.content_digest
    assert other_input != original_input


def _public_canary_examples(kind: str) -> tuple[RelationalLeakageExampleV3, ...]:
    if kind not in {"coefficient", "nonlinear-order", "order", "prompt"}:
        raise ValueError("unsupported test canary")
    legend = "; ".join(
        [f"v-{index:02d} means target slot {index:02d}" for index in range(1, 9)]
        + ["v-09 means source alpha", "v-10 means source beta"]
    )
    examples: list[RelationalLeakageExampleV3] = []
    for group_index in range(2):
        for offset, fault_line in enumerate(range(3, 11)):
            mechanism = "alpha-heavy" if offset % 2 == 0 else "beta-heavy"
            if kind == "prompt":
                prefix = (
                    f"Solver-visible canary: fault-line={fault_line}; "
                    f"mechanism={mechanism}. "
                )
            else:
                prefix = "Solver-visible arithmetic canary. "
            prompt = (
                f"{prefix}Canary instance group={group_index} slot={offset}. "
                f"Case-permuted alias key: {legend}. Decide whether the trace follows."
            )
            lines: list[tuple[int, str]] = []
            for line_number, target_index in zip(range(3, 11), range(1, 9), strict=True):
                basis = "2*v-09+3*v-10"
                if kind == "coefficient" and line_number == fault_line:
                    basis = (
                        "11*v-09+3*v-10"
                        if mechanism == "alpha-heavy"
                        else "2*v-09+11*v-10"
                    )
                elif kind == "order" and mechanism == "beta-heavy":
                    basis = "3*v-10+2*v-09"
                elif kind == "nonlinear-order":
                    basis = (
                        "((v-09*v-10)+0)"
                        if mechanism == "alpha-heavy"
                        else "((v-10*v-09)+0)"
                    )
                lines.append(
                    (
                        line_number,
                        f"claim v-{target_index:02d} = 0; basis {basis}.",
                    )
                )
            examples.append(
                RelationalLeakageExampleV3(
                    group=f"canary-group-{group_index}",
                    group_binding_digest=_digest(f"group:{group_index}"),
                    case_binding_digest=_digest(f"{kind}:{group_index}:{offset}"),
                    family="public-leakage-canary",
                    mechanism=mechanism,
                    fault_line=fault_line,
                    solver_view=RelationalSolverViewV3(
                        prompt=prompt,
                        candidate_lines=tuple(lines),
                    ),
                )
            )
    return tuple(examples)


@pytest.mark.parametrize("kind", ["coefficient", "prompt"])
def test_solver_visible_canaries_block_the_release_gate(kind: str) -> None:
    report = _evaluate(_public_canary_examples(kind))

    assert report.total_cases == 16
    assert report.fault_line_accuracy == 1.0
    assert report.mechanism_accuracy == 1.0
    assert report.worst_group_fault_line_accuracy == 1.0
    assert report.worst_group_mechanism_accuracy == 1.0
    assert report.release_gate_passed is False


def test_rendered_term_order_canary_blocks_on_mechanism() -> None:
    report = _evaluate(_public_canary_examples("order"))

    assert report.fault_line_accuracy == 0.125
    assert report.mechanism_accuracy == 1.0
    assert report.release_gate_passed is False


def test_nonlinear_rendered_order_canary_blocks_on_mechanism() -> None:
    report = _evaluate(_public_canary_examples("nonlinear-order"))

    assert report.fault_line_accuracy == 0.125
    assert report.mechanism_accuracy == 1.0
    assert report.release_gate_passed is False


def _numeric_legend_canary_examples() -> tuple[RelationalLeakageExampleV3, ...]:
    examples: list[RelationalLeakageExampleV3] = []
    for group_index in range(2):
        for offset, fault_line in enumerate(range(3, 11)):
            mechanism = "alpha-coded" if offset % 2 == 0 else "beta-coded"
            target_legend = [
                f"v-{target:02d} means target root code "
                f"{99 if line_number == fault_line else 0}"
                for line_number, target in zip(range(3, 11), range(1, 9), strict=True)
            ]
            source_legend = [
                "v-09 means source alpha mechanism code "
                f"{99 if mechanism == 'alpha-coded' else 0}",
                "v-10 means source beta mechanism code "
                f"{99 if mechanism == 'beta-coded' else 0}",
            ]
            legend = "; ".join([*target_legend, *source_legend])
            view = RelationalSolverViewV3(
                prompt=(
                    f"Numeric legend canary group={group_index} slot={offset}. "
                    f"Case-permuted alias key: {legend}. Decide whether the trace follows."
                ),
                candidate_lines=tuple(
                    (
                        line_number,
                        f"claim v-{target:02d} = 0; basis v-09+v-10.",
                    )
                    for line_number, target in zip(
                        range(3, 11), range(1, 9), strict=True
                    )
                ),
            )
            examples.append(
                RelationalLeakageExampleV3(
                    group=f"numeric-legend-group-{group_index}",
                    group_binding_digest=_digest(f"numeric-legend-group:{group_index}"),
                    case_binding_digest=_digest(
                        f"numeric-legend:{group_index}:{offset}"
                    ),
                    family="numeric-legend-canary",
                    mechanism=mechanism,
                    fault_line=fault_line,
                    solver_view=view,
                )
            )
    return tuple(examples)


def test_numeric_legend_canary_blocks_both_labels() -> None:
    report = _evaluate(_numeric_legend_canary_examples())

    assert report.fault_line_accuracy == 1.0
    assert report.mechanism_accuracy == 1.0
    assert report.release_gate_passed is False


def _ordered_binding_cross_product_examples() -> tuple[RelationalLeakageExampleV3, ...]:
    examples: list[RelationalLeakageExampleV3] = []
    mechanisms = ("forward-binding", "reverse-binding")
    for group_index in range(2):
        for mechanism_index, mechanism in enumerate(mechanisms):
            targets = [
                f"v-{index:02d} means target slot {index:02d}"
                for index in range(1, 9)
            ]
            if mechanism_index:
                targets.reverse()
                sources = [
                    "v-10 means source slot 01",
                    "v-09 means source slot 02",
                ]
            else:
                sources = [
                    "v-09 means source slot 01",
                    "v-10 means source slot 02",
                ]
            legend = "; ".join([*targets, *sources])
            for root_offset, fault_line in enumerate(range(3, 11)):
                variant = (root_offset + 3 * group_index) % 8
                declarations = (
                    range(20, 28) if not mechanism_index else range(27, 19, -1)
                )
                candidate_lines = []
                for line_offset, (line_number, target, declaration) in enumerate(
                    zip(
                        range(3, 11),
                        range(1, 9),
                        declarations,
                        strict=True,
                    )
                ):
                    alpha_first = (line_offset + mechanism_index) % 2 == 0
                    basis = (
                        "((11*v-09)+(3*v-10))"
                        if alpha_first
                        else "((3*v-10)+(11*v-09))"
                    )
                    candidate_lines.append(
                        (
                            line_number,
                            f"claim v-{target:02d} = {declaration}; basis {basis}.",
                        )
                    )
                examples.append(
                    RelationalLeakageExampleV3(
                        group=f"ordered-binding-group-{group_index}",
                        group_binding_digest=_digest(
                            f"ordered-binding-group:{group_index}"
                        ),
                        case_binding_digest=_digest(
                            "ordered-binding:"
                            f"{group_index}:{mechanism_index}:{root_offset}"
                        ),
                        family="ordered-binding-cross-product",
                        mechanism=mechanism,
                        fault_line=fault_line,
                        solver_view=RelationalSolverViewV3(
                            prompt=(
                                "Ordered public binding audit "
                                f"group {group_index} variant q{variant}. "
                                f"Case-permuted alias key: {legend}. "
                                "Decide whether the trace follows."
                            ),
                            candidate_lines=tuple(candidate_lines),
                        ),
                    )
                )
    return tuple(examples)


def test_ordered_public_binding_cross_product_blocks_on_mechanism() -> None:
    examples = _ordered_binding_cross_product_examples()
    assert len(examples) == 32
    assert {
        (example.group, example.mechanism, example.fault_line)
        for example in examples
    } == {
        (f"ordered-binding-group-{group}", mechanism, fault_line)
        for group in range(2)
        for mechanism in ("forward-binding", "reverse-binding")
        for fault_line in range(3, 11)
    }

    report = _evaluate(examples)

    assert report.mechanism_accuracy == 1.0
    assert report.release_gate_passed is False


def _rotated_declaration_examples() -> tuple[RelationalLeakageExampleV3, ...]:
    legend = "; ".join(
        [f"v-{index:02d} means target slot {index:02d}" for index in range(1, 9)]
        + ["v-09 means source alpha", "v-10 means source beta"]
    )
    examples: list[RelationalLeakageExampleV3] = []
    mechanisms = ("ascending-cycle", "descending-cycle")
    for group_index in range(2):
        for mechanism_index, mechanism in enumerate(mechanisms):
            base = list(range(20, 28))
            if mechanism_index:
                base.reverse()
            for root_offset, fault_line in enumerate(range(3, 11)):
                rotation = (root_offset + 3 * group_index) % 8
                values = base[rotation:] + base[:rotation]
                examples.append(
                    RelationalLeakageExampleV3(
                        group=f"declaration-cycle-group-{group_index}",
                        group_binding_digest=_digest(
                            f"declaration-cycle-group:{group_index}"
                        ),
                        case_binding_digest=_digest(
                            "declaration-cycle:"
                            f"{group_index}:{mechanism_index}:{root_offset}"
                        ),
                        family="declaration-cycle-cross-product",
                        mechanism=mechanism,
                        fault_line=fault_line,
                        solver_view=RelationalSolverViewV3(
                            prompt=(
                                f"Declaration cycle group {group_index} variant q{rotation}. "
                                f"Case-permuted alias key: {legend}. "
                                "Decide whether the trace follows."
                            ),
                            candidate_lines=tuple(
                                (
                                    line_number,
                                    f"claim v-{target:02d} = {value}; "
                                    "basis v-09+v-10.",
                                )
                                for line_number, target, value in zip(
                                    range(3, 11), range(1, 9), values, strict=True
                                )
                            ),
                        ),
                    )
                )
    return tuple(examples)


def test_rotated_declaration_cross_product_blocks_on_structural_order() -> None:
    report = _evaluate(_rotated_declaration_examples())

    assert report.total_cases == 32
    assert report.mechanism_accuracy == 1.0
    assert report.release_gate_passed is False


def _prompt_tail_examples() -> tuple[RelationalLeakageExampleV3, ...]:
    legend = "; ".join(
        [f"v-{index:02d} means target slot {index:02d}" for index in range(1, 9)]
        + ["v-09 means source alpha", "v-10 means source beta"]
    )
    examples: list[RelationalLeakageExampleV3] = []
    mechanisms = ("amber-tail", "indigo-tail")
    for group_index in range(2):
        for mechanism_index, mechanism in enumerate(mechanisms):
            for root_offset, fault_line in enumerate(range(3, 11)):
                variant = (root_offset + 5 * group_index) % 8
                examples.append(
                    RelationalLeakageExampleV3(
                        group=f"prompt-tail-group-{group_index}",
                        group_binding_digest=_digest(
                            f"prompt-tail-group:{group_index}"
                        ),
                        case_binding_digest=_digest(
                            f"prompt-tail:{group_index}:{mechanism_index}:{root_offset}"
                        ),
                        family="prompt-tail-cross-product",
                        mechanism=mechanism,
                        fault_line=fault_line,
                        solver_view=RelationalSolverViewV3(
                            prompt=(
                                f"Prompt tail group {group_index} variant q{variant}. "
                                f"Case-permuted alias key: {legend}. "
                                "Decide whether the trace follows. Return one JSON object "
                                "with the fixed schema. Solver-visible trailer code "
                                f"{'amber' if not mechanism_index else 'indigo'}."
                            ),
                            candidate_lines=tuple(
                                (
                                    line_number,
                                    f"claim v-{target:02d} = 0; basis v-09+v-10.",
                                )
                                for line_number, target in zip(
                                    range(3, 11), range(1, 9), strict=True
                                )
                            ),
                        ),
                    )
                )
    return tuple(examples)


def test_complete_prompt_features_block_a_solver_visible_tail_code() -> None:
    report = _evaluate(_prompt_tail_examples())

    assert report.total_cases == 32
    assert report.mechanism_accuracy == 1.0
    assert report.release_gate_passed is False


def test_feature_extractor_has_solver_view_only_capability() -> None:
    assert tuple(inspect.signature(extract_relational_solver_features_v3).parameters) == (
        "view",
    )
    view_fields = set(RelationalSolverViewV3.__dataclass_fields__)
    assert view_fields == {"candidate_lines", "content_digest", "prompt"}
    forbidden = {
        "case_binding_digest",
        "family",
        "fault_line",
        "group",
        "group_binding_digest",
        "mechanism",
    }
    assert not forbidden & view_fields

    class SolverViewSubclass(RelationalSolverViewV3):
        pass

    example = _public_canary_examples("coefficient")[0]
    with pytest.raises(ValueError, match="exact solver view"):
        extract_relational_solver_features_v3(example)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="subclasses are forbidden"):
        SolverViewSubclass(
            prompt=example.prompt,
            candidate_lines=example.candidate_lines,
        )


def test_evaluator_extracts_exact_solver_views_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    examples = _public_canary_examples("coefficient")
    original = extract_relational_solver_features_v3
    seen: list[RelationalSolverViewV3] = []

    def spy(view: RelationalSolverViewV3) -> RelationalSolverFeaturesV3:
        assert type(view) is RelationalSolverViewV3
        assert not hasattr(view, "mechanism")
        assert not hasattr(view, "fault_line")
        seen.append(view)
        return original(view)

    monkeypatch.setattr(relational_module, "extract_relational_solver_features_v3", spy)
    _evaluate(examples)
    assert tuple(seen) == tuple(example.solver_view for example in examples)


def test_solver_feature_digest_is_label_noninterfering_and_visible_sensitive() -> None:
    original = _public_canary_examples("coefficient")
    relabeled = tuple(
        replace(
            example,
            group=f"relabeled-{example.group}",
            group_binding_digest=_digest(f"relabeled:{example.group_binding_digest}"),
            case_binding_digest=_digest(f"relabeled:{example.case_binding_digest}"),
            family="different-hidden-family",
            mechanism=(
                "beta-heavy" if example.mechanism == "alpha-heavy" else "alpha-heavy"
            ),
            fault_line=3 + ((example.fault_line - 3 + 1) % 8),
        )
        for example in original
    )
    original_report = _evaluate(original)
    relabeled_report = _evaluate(relabeled)
    assert original_report.input_digest != relabeled_report.input_digest
    assert original_report.solver_feature_digest == relabeled_report.solver_feature_digest

    first = original[0]
    line_number, line = first.candidate_lines[0]
    changed_view = RelationalSolverViewV3(
        prompt=first.prompt,
        candidate_lines=(
            (line_number, line.replace("3*v-10", "4*v-10", 1)),
            *first.candidate_lines[1:],
        ),
    )
    changed = (replace(first, solver_view=changed_view), *original[1:])
    changed_report = _evaluate(changed)
    assert changed_report.solver_feature_digest != original_report.solver_feature_digest


def test_parser_rejects_alias_prefix_and_duplicate_claim_target() -> None:
    example = _public_canary_examples("coefficient")[0]
    line_number, line = example.candidate_lines[0]
    bad_alias = RelationalSolverViewV3(
        prompt=example.prompt,
        candidate_lines=(
            (line_number, line.replace("v-09", "v-090", 1)),
            *example.candidate_lines[1:],
        ),
    )
    with pytest.raises(ValueError, match="malformed public alias"):
        extract_relational_solver_features_v3(bad_alias)

    duplicate = RelationalSolverViewV3(
        prompt=example.prompt,
        candidate_lines=(
            (line_number, f"{line} claim v-02 = 0; basis v-09+v-10."),
            *example.candidate_lines[1:],
        ),
    )
    with pytest.raises(ValueError, match="multiple claim targets"):
        extract_relational_solver_features_v3(duplicate)


def test_affine_parser_accepts_native_parentheses_and_rejects_nonlinearity() -> None:
    legend = {
        "v-09": "source alpha",
        "v-10": "source beta",
        "v-11": "source gamma",
    }

    assert relational_module._canonical_affine_terms(
        "((40*v-09)+(50*v-10)+(90*v-11))", legend
    ) == (
        (0, "v-09", Fraction(40)),
        (1, "v-10", Fraction(50)),
        (2, "v-11", Fraction(90)),
    )
    for expression in (
        "v-09*v-10+v-11",
        "(v-09+v-10)/v-11",
        "((40*v-09)+(50*v-10)",
        "01*v-09+2*v-10+3*v-11",
    ):
        assert relational_module._canonical_affine_terms(expression, legend) is None


def test_plane_consensus_never_localizes_degenerate_or_tied_points() -> None:
    tetrahedron = (
        (Fraction(0), Fraction(0), Fraction(0)),
        (Fraction(1), Fraction(0), Fraction(0)),
        (Fraction(0), Fraction(1), Fraction(0)),
        (Fraction(0), Fraction(0), Fraction(1)),
    )
    maximum, outliers, ties = relational_module._plane_support(tetrahedron)
    assert (maximum, outliers, ties) == (3, set(), 4)

    collinear = tuple(
        (Fraction(value), Fraction(2 * value), Fraction(3 * value))
        for value in range(5)
    )
    maximum, outliers, ties = relational_module._plane_support(collinear)
    assert (maximum, outliers, ties) == (0, set(), 0)


def test_high_cardinality_binding_features_require_training_recurrence() -> None:
    bound_feature = "bound-line-surface::3::sha256:" + "a" * 64
    prompt_feature = "prompt-complete::token::solver-visible-tail-code"
    ordinary_feature = "target::source alpha"

    below_threshold = relational_module._fit_binary_presence_model(
        [frozenset({bound_feature, ordinary_feature}) for _ in range(7)],
        [frozenset() for _ in range(8)],
    )
    at_threshold = relational_module._fit_binary_presence_model(
        [frozenset({bound_feature, ordinary_feature}) for _ in range(8)],
        [frozenset() for _ in range(8)],
    )

    assert bound_feature not in below_threshold.weights
    assert ordinary_feature in below_threshold.weights
    assert bound_feature in at_threshold.weights

    prompt_below_threshold = relational_module._fit_binary_presence_model(
        [frozenset({prompt_feature}) for _ in range(3)],
        [frozenset() for _ in range(4)],
    )
    prompt_at_threshold = relational_module._fit_binary_presence_model(
        [frozenset({prompt_feature}) for _ in range(4)],
        [frozenset() for _ in range(4)],
    )
    assert prompt_feature not in prompt_below_threshold.weights
    assert prompt_feature in prompt_at_threshold.weights


def test_exact_public_coefficient_geometry_localizes_a_seven_row_plane_outlier() -> None:
    legend = "; ".join(
        [f"v-{index:02d} means target slot {index:02d}" for index in range(1, 9)]
        + [
            "v-09 means source alpha",
            "v-10 means source beta",
            "v-11 means source gamma",
        ]
    )
    points = (
        (1, 1, 2),
        (1, 2, 3),
        (2, 1, 3),
        (2, 2, 4),
        (3, 1, 4),
        (1, 3, 4),
        (3, 2, 5),
        (3, 3, 99),
    )
    orders = (
        (0, 1, 2),
        (1, 2, 0),
        (2, 0, 1),
        (0, 2, 1),
        (1, 0, 2),
        (2, 1, 0),
        (1, 2, 0),
        (2, 0, 1),
    )
    aliases = ("v-09", "v-10", "v-11")
    view = RelationalSolverViewV3(
        prompt=f"Case-permuted alias key: {legend}. Decide whether the trace follows.",
        candidate_lines=tuple(
            (
                line_number,
                f"claim v-{target:02d} = 0; basis ("
                + "+".join(
                    f"({point[source]}*{aliases[source]})" for source in order
                )
                + ").",
            )
            for line_number, target, point, order in zip(
                range(3, 11), range(1, 9), points, orders, strict=True
            )
        ),
    )
    bundle = extract_relational_solver_features_v3(view)

    assert "semantic-plane::maximum-support::7" in bundle.case_features
    assert "semantic-plane::outlier-count::1" in bundle.case_features
    assert "position-plane::maximum-support::7" not in bundle.case_features
    assert "semantic-plane::outlier-candidate::True" in bundle.features_for_line(10)
    assert all(
        "semantic-plane::outlier-candidate::False" in bundle.features_for_line(line_number)
        for line_number in range(3, 10)
    )


def test_nonlinear_visible_coefficient_geometry_localizes_the_public_outlier() -> None:
    legend = "; ".join(
        [f"v-{index:02d} means target slot {index:02d}" for index in range(1, 9)]
        + [
            "v-09 means source alpha",
            "v-10 means source beta",
            "v-11 means source gamma",
        ]
    )
    points = (
        (20, 30, 50),
        (20, 31, 51),
        (21, 30, 51),
        (21, 31, 52),
        (22, 30, 52),
        (20, 32, 52),
        (22, 31, 53),
        (22, 32, 59),
    )
    view = RelationalSolverViewV3(
        prompt=f"Case-permuted alias key: {legend}. Decide whether the trace follows.",
        candidate_lines=tuple(
            (
                line_number,
                f"claim v-{target:02d} = 0; basis "
                f"(({alpha}*v-09)*({beta}*v-10)+({gamma}*v-11)).",
            )
            for line_number, target, (alpha, beta, gamma) in zip(
                range(3, 11), range(1, 9), points, strict=True
            )
        ),
    )

    assert relational_module._canonical_affine_terms(
        "((20*v-09)*(30*v-10)+(50*v-11))",
        relational_module._legend(view.prompt),
    ) is None
    bundle = extract_relational_solver_features_v3(view)

    assert "semantic-plane::maximum-support::7" in bundle.case_features
    assert "semantic-plane::outlier-count::1" in bundle.case_features
    assert "semantic-plane::outlier-candidate::True" in bundle.features_for_line(10)


def test_coefficient_sign_attachment_and_rendered_order_change_features() -> None:
    example = _public_canary_examples("coefficient")[0]
    line_number, root = example.candidate_lines[0]
    variants = (
        root.replace("11*v-09", "-11*v-09", 1),
        root.replace("11*v-09+3*v-10", "3*v-09+11*v-10", 1),
        root.replace("11*v-09+3*v-10", "3*v-10+11*v-09", 1),
    )
    original = extract_relational_solver_features_v3(example.solver_view)
    original_line_features = original.features_for_line(line_number)
    variant_digests = set()
    for variant in variants:
        view = RelationalSolverViewV3(
            prompt=example.prompt,
            candidate_lines=((line_number, variant), *example.candidate_lines[1:]),
        )
        bundle = extract_relational_solver_features_v3(view)
        variant_digests.add(bundle.content_digest)
        assert bundle.content_digest != original.content_digest
        assert bundle.features_for_line(line_number) != original_line_features
    assert len(variant_digests) == len(variants)
