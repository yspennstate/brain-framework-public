import copy
import math
import random
import re
from collections import Counter, defaultdict
from dataclasses import replace
from fractions import Fraction
from itertools import combinations

import pytest

import math_brain.hard_challenge_v2 as hard_v2
from math_brain.benchmark import SolverView
from math_brain.calibration_v2 import (
    DEFAULT_CONFIG,
    BaselineKind,
    CalibrationFreeze,
    generate_calibration_report,
)
from math_brain.hard_challenge_v2 import (
    ANSWER_SCHEMA_V2,
    CALIBRATION_CASE_COUNT_V2,
    CALIBRATION_ELIGIBLE_FAULT_LINES_V2,
    CALIBRATION_SCHEDULE_VERSION_V2,
    DERIVATION_FAMILY_ORDER_V2,
    FAMILY_ORDER_V2,
    FAULT_MECHANISM_ORDER_V2,
    INTERPRETER_SCHEMA_V2,
    MAX_BANK_SEEDS_V2,
    MAX_CASE_JSON_BYTES_V2,
    MAX_PROMPT_BYTES_V2,
    PARAMETER_CELL_ORDER_V2,
    DiagnosticWitnessV2,
    FamilyV2,
    FaultMechanismV2,
    HardChallengeCaseV2,
    MathValueKindV2,
    NormalizedMathValueV2,
    RepairSubmissionV2,
    SolverOutputV2,
    UniversalWitnessCodeV2,
    WitnessKindV2,
    adapt_calibration_case_v2,
    adapt_public_calibration_bank_v2,
    calibration_schedule_digest_v2,
    canonical_json_v2,
    certification_report_v2,
    content_digest_v2,
    encode_development_seed_v2,
    execute_semantic_program_v2,
    generate_development_case_v2,
    generate_public_calibration_bank_v2,
    generate_public_development_bank_v2,
    strict_json_loads_v2,
)


def _all_cases(seed_count: int = 64):
    return generate_public_development_bank_v2(range(seed_count))


def _calibration_cases():
    return generate_public_calibration_bank_v2()


def _public_alias_legend(prompt: str) -> dict[str, str]:
    marker = "Case-permuted alias key: "
    start = prompt.index(marker) + len(marker)
    end = prompt.index(". Decide whether", start)
    entries = prompt[start:end].split("; ")
    result = dict(entry.split(" means ", maxsplit=1) for entry in entries)
    assert all(re.fullmatch(r"v-[0-9]{2}", alias) for alias in result)
    return result


def _public_case_tag(prompt: str) -> str:
    value = prompt.split(". ", maxsplit=1)[0].removeprefix("Opaque case tag ")
    assert re.fullmatch(r"[0-9]{20}", value)
    return value


def _surface_claim(line: str) -> tuple[str, Fraction, str]:
    match = re.search(
        r"claim (v-[0-9]{2}) =\s*([^;]+); basis (.+?)\.(?: \[checkpoint\])?$",
        line,
    )
    assert match is not None, line
    return match.group(1), Fraction(match.group(2).strip()), match.group(3)


def _visible_expected_claim_value(prompt: str, target_alias: str) -> Fraction:
    """Recompute a target using only its public prompt and public alias meaning."""

    description = _public_alias_legend(prompt)[target_alias]
    if "simultaneous solution" in prompt:
        match = re.search(
            r"x≡(-?[0-9]+) \(mod ([0-9]+)\) and x≡(-?[0-9]+) \(mod ([0-9]+)\)",
            prompt,
        )
        assert match is not None
        r1, m1, r2, m2 = map(int, match.groups())
        period = math.lcm(m1, m2)
        x0 = next(value for value in range(period) if value % m1 == r1 and value % m2 == r2)
        g = math.gcd(m1, m2)
        quotient = (((r2 - r1) // g) * pow(m1 // g, -1, m2 // g)) % (m2 // g)
        expected = {
            "the solution-class period lcm(m1,m2)": period,
            "the next solution x0+lcm(m1,m2)": x0 + period,
            "the span of two solution periods 2*lcm(m1,m2)": 2 * period,
            "the solution x0+2*lcm(m1,m2)": x0 + 2 * period,
            "the period-plus-gcd audit lcm(m1,m2)+gcd(m1,m2)": period + g,
            "the period plus the first reduced modulus": period + m1 // g,
            "the period plus the second reduced modulus": period + m2 // g,
            "the period plus the least CRT quotient": period + quotient,
        }
        return Fraction(expected[description])
    if "count of triples" in prompt:
        match = re.search(
            r"x1\+x2\+x3=([0-9]+) and 0<=x1<=([0-9]+), "
            r"0<=x2<=([0-9]+), 0<=x3<=([0-9]+)",
            prompt,
        )
        assert match is not None
        total, b1, b2, b3 = map(int, match.groups())

        def stars(value: int) -> int:
            return math.comb(value + 2, 2) if value >= 0 else 0

        unbounded = stars(total)
        singles = tuple(stars(total - bound - 1) for bound in (b1, b2, b3))
        single_total = sum(singles)
        joint = stars(total - (b1 + 1) - (b2 + 1))
        expected = {
            "the intersection count J for violating the first two bounds": joint,
            "the corrected bounded count U-S+J": unbounded - single_total + joint,
            "J plus the first single-bound violation count": joint + singles[0],
            "J plus the second single-bound violation count": joint + singles[1],
            "J plus the third single-bound violation count": joint + singles[2],
            "twice the true joint intersection count 2*J": 2 * joint,
            "J plus the required coordinate total": joint + total,
            "the unbounded count plus J": unbounded + joint,
        }
        return Fraction(expected[description])
    if "shortest-path analysis" in prompt:
        match = re.search(
            r"s→a:(-?[0-9]+), s→b:(-?[0-9]+), b→a:(-?[0-9]+), "
            r"a→t:(-?[0-9]+), b→t:(-?[0-9]+), and s→t:(-?[0-9]+)",
            prompt,
        )
        assert match is not None
        sa, sb, ba, at, bt, st = map(int, match.groups())
        incoming = min(sa, sb + ba)
        expected = {
            "the shortest complete incoming-path length A at a": incoming,
            "the shortest s-to-t path length A+weight(a,t)": incoming + at,
            "the direct-label improvement length(s,a)-A": sa - incoming,
            "A plus weight(b,t)": incoming + bt,
            "A plus weight(s,t)": incoming + st,
            "twice the complete incoming-path length 2*A": 2 * incoming,
            "A minus weight(b,a)": incoming - ba,
            "A plus the direct s-to-b path length": incoming + sb,
        }
        return Fraction(expected[description])
    match = re.search(
        r"a_\(n\+2\)-([0-9]+)a_\(n\+1\)\+([0-9]+)a_n=([0-9]+)\^n "
        r"with a_0=(-?[0-9]+), a_1=(-?[0-9]+)",
        prompt,
    )
    assert match is not None
    middle, constant, root, c0, c1 = map(int, match.groups())
    assert middle == 2 * root and constant == root * root
    degree = 2
    coefficient = Fraction(1, 2 * root * root)
    expected = {
        "the required resonance multiplier degree D": degree,
        "the degree audit D+1": degree + 1,
        "twice the required degree 2*D": 2 * degree,
        "the required degree plus the root D+r": degree + root,
        "the required degree plus the initial value D+a_0": degree + c0,
        "the required degree plus the initial value D+a_1": degree + c1,
        "the predecessor degree D-1": degree - 1,
        "the coefficient-weighted degree D*K": degree * coefficient,
    }
    return Fraction(expected[description])


def _transition_signature(case: HardChallengeCaseV2) -> tuple[tuple[object, ...], ...]:
    return tuple(
        (
            transition.kind.value,
            transition.target,
            canonical_json_v2(transition.expression.to_dict()),
            canonical_json_v2(transition.surface_expression.to_dict()),
            (
                None
                if transition.declared is None
                else canonical_json_v2(transition.declared.to_dict())
            ),
        )
        for transition in case.semantic_program.transitions(faulty=False)
    )


def _rehash(raw: dict) -> dict:
    result = copy.deepcopy(raw)
    result["content_digest"] = content_digest_v2(
        {key: value for key, value in result.items() if key != "content_digest"}
    )
    return result


def test_axes_are_independent_and_conditionally_uniform() -> None:
    cases = _calibration_cases()
    assert len(cases) == CALIBRATION_CASE_COUNT_V2 == 320
    assert {case.template_id for case in cases} == {
        "public-hard-v2/discourse-0",
        "public-hard-v2/discourse-1",
        "public-hard-v2/discourse-2",
        "public-hard-v2/discourse-3",
    }
    assert {case.derivation_family for case in cases} == {
        family.value for family in DERIVATION_FAMILY_ORDER_V2
    }
    assert {case.generator_cell for case in cases} == {
        f"public-hard-v2/parameter-{cell.value}" for cell in PARAMETER_CELL_ORDER_V2
    }
    for case in cases:
        assert case.position == case.seed % 8
        assert case.template == (case.seed // 8) % 4
        assert case.derivation_family_index == (case.seed // 32) % 4
        assert case.fault_variant == (case.seed // 128) % 2
        assert case.parameter_cell == (case.seed // 256) % 2
        assert case.instance == case.seed // 512
        assert case.root_line == 3 + case.position
        assert case.template_id == f"public-hard-v2/discourse-{case.template}"


def test_surface_aliases_and_claim_role_ignore_position_and_fault_axes() -> None:
    for family in FAMILY_ORDER_V2:
        variants = tuple(
            generate_development_case_v2(
                family,
                encode_development_seed_v2(
                    position=position,
                    template=2,
                    derivation_family=3,
                    fault_variant=fault,
                    parameter_cell=1,
                    instance=7,
                ),
            )
            for position in range(8)
            for fault in range(2)
        )
        assert (
            len(
                {
                    canonical_json_v2(dict(case.semantic_program.surface_aliases))
                    for case in variants
                }
            )
            == 1
        )
        assert {case.semantic_program.clean_root.target for case in variants} == {
            variants[0].semantic_program.clean_root.target
        }
        assert {_public_case_tag(case.prompt) for case in variants} == {
            _public_case_tag(variants[0].prompt)
        }

        next_instance = generate_development_case_v2(
            family,
            encode_development_seed_v2(
                position=0,
                template=2,
                derivation_family=3,
                fault_variant=0,
                parameter_cell=1,
                instance=8,
            ),
        )
        assert dict(next_instance.semantic_program.surface_aliases) != dict(
            variants[0].semantic_program.surface_aliases
        )

        all_noninstance_axes = tuple(
            generate_development_case_v2(
                family,
                encode_development_seed_v2(
                    position=0,
                    template=template,
                    derivation_family=derivation,
                    fault_variant=0,
                    parameter_cell=cell,
                    instance=7,
                ),
            )
            for template in range(4)
            for derivation in range(4)
            for cell in range(2)
        )
        assert (
            len(
                {
                    canonical_json_v2(dict(case.semantic_program.surface_aliases))
                    for case in all_noninstance_axes
                }
            )
            == 1
        )
        assert {_public_case_tag(case.prompt) for case in all_noninstance_axes} == {
            _public_case_tag(variants[0].prompt)
        }

    calibration = _calibration_cases()
    role_support = Counter(case.semantic_program.clean_root.target for case in calibration)
    assert role_support == Counter({role: 40 for role in hard_v2._CLAIM_NAMES_V2})
    role_mechanism = Counter(
        (case.semantic_program.clean_root.target, case.mechanism) for case in calibration
    )
    assert len(role_mechanism) == 8 * 2
    assert min(role_mechanism.values()) >= 14
    role_position = Counter(
        (case.semantic_program.clean_root.target, case.position) for case in calibration
    )
    assert len(role_position) == 8 * 8


def test_numeric_instance_is_constant_across_non_parameter_axes() -> None:
    axis_points = {(position, template, 0, 0) for position in range(8) for template in range(4)}
    axis_points.update(
        (position, template, derivation, fault)
        for position, template in ((0, 0), (7, 3))
        for derivation in range(4)
        for fault in range(2)
    )
    for family in FAMILY_ORDER_V2:
        for parameter_cell in range(2):
            for instance in range(2):
                cases = [
                    generate_development_case_v2(
                        family,
                        encode_development_seed_v2(
                            position=position,
                            template=template,
                            derivation_family=derivation,
                            fault_variant=fault,
                            parameter_cell=parameter_cell,
                            instance=instance,
                        ),
                    )
                    for position, template, derivation, fault in axis_points
                ]
                assert len({canonical_json_v2(dict(case.parameters)) for case in cases}) == 1
                assert {case.position for case in cases} == set(range(8))
                assert {case.template for case in cases} == set(range(4))
                assert {case.derivation_family_index for case in cases} == set(range(4))
                assert {case.fault_variant for case in cases} == {0, 1}


def test_four_discourse_renderers_are_distinct_whole_trace_forms() -> None:
    cases = [
        generate_development_case_v2(FamilyV2.GENERALIZED_CRT_PERIOD, seed)
        for seed in (0, 8, 16, 24)
    ]
    first_lines = [case.trace(faulty=True)[0] for case in cases]
    assert first_lines[0].startswith("1. Record s01 [verify]:")
    assert first_lines[1].startswith("1. Ledger s01 [verify] ::")
    assert first_lines[2].startswith("1. Using s01 [verify], register")
    assert first_lines[3].startswith("1. [verify]")
    assert first_lines[3].endswith("[checkpoint]")
    assert len({case.trace(faulty=True) for case in cases}) == 4


def test_fault_mechanisms_form_complete_balanced_cartesian_cells() -> None:
    cases = _calibration_cases()
    mechanisms = {mechanism.value for mechanism in FAULT_MECHANISM_ORDER_V2}
    assert mechanisms == {
        FaultMechanismV2.SINGLE_SOURCE_SHORTCUT.value,
        FaultMechanismV2.DUPLICATED_CORRECTION.value,
    }

    family_template = defaultdict(list)
    for case in cases:
        family_template[(case.derivation_family, case.template)].append(case)
    assert len(family_template) == 4 * 4
    for values in family_template.values():
        assert len(values) == 20
        assert len({case.domain for case in values}) == 4
        assert Counter(case.mechanism for case in values) == Counter(
            {mechanism: 10 for mechanism in mechanisms}
        )
        assert Counter(case.generator_cell for case in values) == Counter(
            {cell: 10 for cell in {case.generator_cell for case in cases}}
        )
        root_counts = Counter(case.root_line for case in values)
        assert sorted(root_counts.values()) == [2, 2, 2, 2, 3, 3, 3, 3]
        probabilities = [count / 20 for count in root_counts.values()]
        entropy = -sum(probability * math.log2(probability) for probability in probabilities)
        assert entropy / 3 > 0.99
        assert max(probabilities) == pytest.approx(0.15)

    family_template_mechanism = Counter(
        (case.derivation_family, case.template, case.mechanism) for case in cases
    )
    assert len(family_template_mechanism) == 4 * 4 * 2
    assert set(family_template_mechanism.values()) == {10}

    domain_mechanism = Counter((case.domain, case.mechanism) for case in cases)
    assert len(domain_mechanism) == 4 * 2
    assert set(domain_mechanism.values()) == {40}
    domain_mechanism_root = Counter((case.domain, case.mechanism, case.position) for case in cases)
    assert len(domain_mechanism_root) == 4 * 2 * 8
    assert min(domain_mechanism_root.values()) >= 2
    assert max(domain_mechanism_root.values()) <= 10

    conditioned: dict[tuple[str, int, int], list[HardChallengeCaseV2]] = defaultdict(list)
    for case in cases:
        conditioned[(case.derivation_family, case.template, case.position)].append(case)
    assert len(conditioned) == 4 * 4 * 8
    for values in conditioned.values():
        assert len(values) in (2, 3)
        assert {case.mechanism for case in values} == mechanisms
        assert len({case.generator_cell for case in values}) == 2

    domain_family_template = defaultdict(list)
    for case in cases:
        domain_family_template[(case.domain, case.derivation_family, case.template)].append(case)
    assert len(domain_family_template) == 4 * 4 * 4
    for values in domain_family_template.values():
        assert len(values) == 5
        assert {(case.mechanism, case.generator_cell) for case in values} == {
            (mechanism, cell)
            for mechanism in mechanisms
            for cell in {case.generator_cell for case in cases}
        }

    best_domain_only_accuracy = sum(
        max(
            count
            for (candidate_domain, _), count in domain_mechanism.items()
            if candidate_domain == domain
        )
        for domain in {case.domain for case in cases}
    ) / len(cases)
    assert best_domain_only_accuracy == pytest.approx(0.5)


def test_paired_faults_have_distinct_typed_claims_and_repair_descriptions() -> None:
    for family in FAMILY_ORDER_V2:
        for parameter_cell in range(2):
            paired = [
                generate_development_case_v2(
                    family,
                    encode_development_seed_v2(
                        position=4,
                        template=2,
                        derivation_family=3,
                        fault_variant=fault,
                        parameter_cell=parameter_cell,
                        instance=2,
                    ),
                )
                for fault in range(2)
            ]
            first, second = paired
            assert first.semantic_program.faulty_root.surface_expression != (
                second.semantic_program.faulty_root.surface_expression
            )
            assert first.semantic_program.faulty_root.declared != (
                second.semantic_program.faulty_root.declared
            )
            assert first.faulty_replacement != second.faulty_replacement
            assert first.repair_description != second.repair_description
            assert certification_report_v2(first).passed
            assert certification_report_v2(second).passed


def test_public_views_have_no_fault_axis_field_or_stable_mechanism_token() -> None:
    cases = _calibration_cases()
    mechanism_counts_by_token: dict[str, Counter[str]] = defaultdict(Counter)
    for case in cases:
        solver_text = case.faulty_solver_view.canonical_json()
        hidden_markers = {
            "fault_variant",
            "derivation_family",
            "generator_cell",
            "parameter_cell",
            "parameter_construction",
            case.mechanism,
            case.repair_description,
            case.derivation_family,
            case.generator_cell,
            case.parameter_construction,
            CALIBRATION_SCHEDULE_VERSION_V2,
            *(mechanism.value for mechanism in FAULT_MECHANISM_ORDER_V2),
        }
        assert all(marker not in solver_text for marker in hidden_markers)
        for token in set(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", solver_text)):
            mechanism_counts_by_token[token][case.mechanism] += 1

    for token, counts in mechanism_counts_by_token.items():
        if sum(counts.values()) >= 20:
            assert set(counts) == {
                FaultMechanismV2.SINGLE_SOURCE_SHORTCUT.value,
                FaultMechanismV2.DUPLICATED_CORRECTION.value,
            }, token
        assert len(set(counts.values())) == 1, token


def test_solver_visible_relational_join_is_an_explicit_release_blocker() -> None:
    """Pin the perfect non-mathematical decoder that superseded pilot v1.

    Future generator work must first make these accuracies fall to the declared
    0.15/0.55 leakage limits, then replace this blocker regression and remove the
    collector kill switch in one reviewed change.
    """

    cues = {
        "generalized_crt_period": (
            ("raw modulus product", "single_source_shortcut", 0),
            ("twice-reduced product", "duplicated_correction", 0),
        ),
        "bounded_composition_overlap": (
            ("one upper-bound shift", "single_source_shortcut", 0),
            ("repeating a pair correction", "duplicated_correction", 0),
        ),
        "negative_edge_finalization": (
            ("direct s-to-a path length", "single_source_shortcut", 1),
            ("applying the a-improvement twice", "duplicated_correction", 0),
        ),
        "forced_recurrence_resonance": (
            ("unadjusted single-resonance trial degree 1", "single_source_shortcut", 0),
            ("produced by duplicating the correction", "duplicated_correction", 0),
        ),
    }
    visible_domains = (
        ("description of all integers x satisfying", "generalized_crt_period"),
        ("numbered count of triples", "bounded_composition_overlap"),
        ("numbered shortest-path analysis", "negative_edge_finalization"),
        ("numbered solution of a_(n+2)", "forced_recurrence_resonance"),
    )

    def domain_from_prompt(prompt: str) -> str:
        matches = [domain for marker, domain in visible_domains if marker in prompt]
        assert len(matches) == 1
        return matches[0]

    def legend_from_prompt(prompt: str) -> dict[str, str]:
        raw = prompt.split("Case-permuted alias key: ", 1)[1].split(". Decide", 1)[0]
        return dict(entry.split(" means ", 1) for entry in raw.split("; "))

    def basis(line: str) -> str:
        return line.split("; basis ", 1)[1].rsplit(".", 1)[0]

    def alias_count(alias: str, text: str) -> int:
        pattern = rf"(?<![A-Za-z0-9]){re.escape(alias)}(?![A-Za-z0-9])"
        return len(re.findall(pattern, text))

    line_correct = 0
    mechanism_correct = 0
    cases = _calibration_cases()
    for case in cases:
        domain = domain_from_prompt(case.prompt)
        legend = legend_from_prompt(case.prompt)
        candidate_rows = case.trace(faulty=True)[2:10]
        active: list[tuple[str, str]] = []
        for phrase, mechanism, clean_count in cues[domain]:
            alias = next(alias for alias, meaning in legend.items() if phrase in meaning)
            observed = sum(alias_count(alias, basis(row)) for row in candidate_rows)
            if observed > clean_count:
                active.append((alias, mechanism))
        assert len(active) == 1
        alias, predicted_mechanism = active[0]
        hits = [
            (line_number, row)
            for line_number, row in enumerate(candidate_rows, 3)
            if alias_count(alias, basis(row))
        ]
        if domain == "negative_edge_finalization" and (
            predicted_mechanism == "single_source_shortcut"
        ):
            legitimate_target = next(
                candidate
                for candidate, meaning in legend.items()
                if "direct-label improvement" in meaning
            )
            if len(hits) == 1:
                predicted_line = hits[0][0]
            else:
                predicted_line = next(
                    line_number
                    for line_number, row in hits
                    if f"claim {legitimate_target} " not in row
                )
        else:
            predicted_line = hits[0][0]
        line_correct += predicted_line == case.faulty_line
        mechanism_correct += predicted_mechanism == case.mechanism

    assert line_correct == len(cases) == 320
    assert mechanism_correct == len(cases)


def test_sealed_schedule_tables_have_the_preregistered_digest() -> None:
    assert calibration_schedule_digest_v2() == (
        "sha256:a221e9a48c653e8446bae3f2f0b3260726f7a2dec20f8fe6fdfaf36e11ff1d2b"
    )


def test_derivation_families_and_parameter_cells_change_bound_semantics() -> None:
    for domain in FAMILY_ORDER_V2:
        for parameter_cell in range(2):
            derivations = [
                generate_development_case_v2(
                    domain,
                    encode_development_seed_v2(
                        position=4,
                        template=1,
                        derivation_family=derivation,
                        fault_variant=0,
                        parameter_cell=parameter_cell,
                        instance=2,
                    ),
                )
                for derivation in range(4)
            ]
            assert len({_transition_signature(case) for case in derivations}) == 4
            assert len({case.trace(faulty=False) for case in derivations}) == 4
            assert len({canonical_json_v2(dict(case.parameters)) for case in derivations}) == 1
            assert all(certification_report_v2(case).passed for case in derivations)

        for derivation in range(4):
            cells = [
                generate_development_case_v2(
                    domain,
                    encode_development_seed_v2(
                        position=4,
                        template=1,
                        derivation_family=derivation,
                        fault_variant=0,
                        parameter_cell=parameter_cell,
                        instance=2,
                    ),
                )
                for parameter_cell in range(2)
            ]
            assert cells[0].parameters != cells[1].parameters
            assert cells[0].prompt != cells[1].prompt
            assert cells[0].semantic_program.initial_state != (
                cells[1].semantic_program.initial_state
            )
            assert cells[0].generator_cell != cells[1].generator_cell
            assert all(certification_report_v2(case).passed for case in cells)


def test_schedule_low_order_lookup_leakage_stays_within_held_out_limits() -> None:
    cases = _calibration_cases()

    def value(case: HardChallengeCaseV2, axis: str) -> object:
        return {
            "cell": case.parameter_cell,
            "domain": case.domain,
            "family": case.derivation_family,
            "mechanism": case.fault_variant,
            "root": case.position,
            "template": case.template,
        }[axis]

    def accuracy(
        features: tuple[str, ...],
        *,
        target: str,
        held_family: str | None = None,
        held_template: int | None = None,
    ) -> float:
        training = [
            case
            for case in cases
            if (held_family is None or case.derivation_family != held_family)
            and (held_template is None or case.template != held_template)
        ]
        testing = [
            case
            for case in cases
            if (held_family is None or case.derivation_family == held_family)
            and (held_template is None or case.template == held_template)
        ]
        grouped: dict[tuple[object, ...], Counter[object]] = defaultdict(Counter)
        fallback: Counter[object] = Counter()
        for case in training:
            label = value(case, target)
            grouped[tuple(value(case, axis) for axis in features)][label] += 1
            fallback[label] += 1
        correct = 0
        for case in testing:
            counts = grouped.get(tuple(value(case, axis) for axis in features), fallback)
            predicted = max(sorted(counts), key=lambda label: counts[label])
            correct += predicted == value(case, target)
        return correct / len(testing)

    def maximum_held_out(candidates: tuple[str, ...], *, target: str, mode: str) -> float:
        feature_sets = [
            subset
            for size in range(1, len(candidates) + 1)
            for subset in combinations(candidates, size)
        ]
        scores: list[float] = []
        if mode == "family":
            for features in feature_sets:
                scores.append(
                    sum(
                        accuracy(features, target=target, held_family=family.value)
                        for family in DERIVATION_FAMILY_ORDER_V2
                    )
                    / 4
                )
        elif mode == "template":
            for features in feature_sets:
                scores.append(
                    sum(
                        accuracy(features, target=target, held_template=template)
                        for template in range(4)
                    )
                    / 4
                )
        else:
            for features in feature_sets:
                scores.append(
                    sum(
                        accuracy(
                            features,
                            target=target,
                            held_family=family.value,
                            held_template=template,
                        )
                        for family in DERIVATION_FAMILY_ORDER_V2
                        for template in range(4)
                    )
                    / 16
                )
        return max(scores)

    assert (
        maximum_held_out(
            ("domain", "template", "cell", "root"),
            target="mechanism",
            mode="family",
        )
        <= 0.55
    )
    assert (
        maximum_held_out(
            ("domain", "family", "cell", "root"),
            target="mechanism",
            mode="template",
        )
        <= 0.55
    )
    assert (
        maximum_held_out(
            ("domain", "cell", "root"),
            target="mechanism",
            mode="joint",
        )
        <= 0.55
    )
    assert (
        maximum_held_out(
            ("domain", "template", "mechanism", "cell"),
            target="root",
            mode="family",
        )
        <= 0.15
    )
    assert (
        maximum_held_out(
            ("domain", "family", "mechanism", "cell"),
            target="root",
            mode="template",
        )
        <= 0.15
    )
    assert (
        maximum_held_out(
            ("domain", "mechanism", "cell"),
            target="root",
            mode="joint",
        )
        <= 0.15
    )

    # This is a held-out design, not an unconditional independence claim.
    in_sample_pair_scores = []
    axes = ("domain", "family", "template", "cell", "root")
    for left_index, left in enumerate(axes):
        for right in axes[left_index + 1 :]:
            grouped: dict[tuple[object, ...], Counter[object]] = defaultdict(Counter)
            for case in cases:
                grouped[(value(case, left), value(case, right))][case.fault_variant] += 1
            in_sample_pair_scores.append(
                sum(max(counts.values()) for counts in grouped.values()) / len(cases)
            )
    assert max(in_sample_pair_scores) == pytest.approx(0.575)


def test_live_calibration_adapter_passes_all_ten_rendered_shortcut_gates() -> None:
    hard_cases = _calibration_cases()
    # This deterministic fixture exercises structural gates only. It is not an
    # empirical model result and is deliberately named nowhere in the report.
    outcomes = {case.case_id: case.position % 2 == 0 for case in hard_cases}
    cases = adapt_public_calibration_bank_v2(outcomes)
    assert len(cases) == CALIBRATION_CASE_COUNT_V2
    for hard_case, case in zip(hard_cases, cases, strict=True):
        assert case.case_id == hard_case.case_id
        assert case.family == hard_case.derivation_family
        assert case.template_id == hard_case.template_id
        assert case.generator_cell == hard_case.generator_cell
        assert case.domain == hard_case.domain
        assert case.fault_kind == hard_case.mechanism
        assert case.faulty_trace == hard_case.trace(faulty=True)
        assert case.eligible_fault_lines == CALIBRATION_ELIGIBLE_FAULT_LINES_V2
        assert case.fault_line == hard_case.faulty_line
        assert not case.receipts_attest_semantic_independence
        assert {receipt.verifier_id for receipt in case.oracles} == {
            f"{hard_case.primary_verifier}@{hard_case.primary_oracle_digest}",
            f"{hard_case.independent_verifier}@{hard_case.independent_oracle_digest}",
        }

    seed = "hard-v2-rendered-structural-gates"
    state = CalibrationFreeze.draft().commit_seed(seed).bind_cases(cases)
    report = generate_calibration_report(cases, state=state, seed=seed)
    assert report.accepted
    assert len(report.baselines) == len(BaselineKind) * 2 == 10
    assert {metric.name for metric in report.baselines} == {
        f"{kind.value}__{suffix}" for kind in BaselineKind for suffix in ("lofo", "within_family")
    }
    assert all(
        metric.excess_over_chance <= DEFAULT_CONFIG.maximum_baseline_excess_over_chance
        and metric.worst_family_excess_over_chance
        <= DEFAULT_CONFIG.maximum_worst_family_baseline_excess_over_chance
        for metric in report.baselines
    )
    assert (
        report.domain_fault_cross_prediction.excess_over_chance
        <= DEFAULT_CONFIG.maximum_mechanism_excess_over_chance
    )
    assert (
        report.domain_fault_cross_prediction.worst_family_excess_over_chance
        <= DEFAULT_CONFIG.maximum_mechanism_excess_over_chance
    )


def test_calibration_adapter_requires_exact_complete_outcomes() -> None:
    hard_cases = _calibration_cases()
    outcomes = {case.case_id: False for case in hard_cases}
    first = hard_cases[0]
    with pytest.raises(ValueError, match="exact boolean"):
        adapt_calibration_case_v2(first, brain_off_correct=1)  # type: ignore[arg-type]

    incomplete = dict(outcomes)
    incomplete.pop(first.case_id)
    with pytest.raises(ValueError, match="cover the fixed calibration bank exactly"):
        adapt_public_calibration_bank_v2(incomplete)

    poisoned = dict(outcomes)
    poisoned[first.case_id] = 0  # type: ignore[assignment]
    with pytest.raises(ValueError, match="exact string-to-boolean"):
        adapt_public_calibration_bank_v2(poisoned)

    class OutcomeDict(dict):
        pass

    with pytest.raises(ValueError, match="exact string-to-boolean"):
        adapt_public_calibration_bank_v2(OutcomeDict(outcomes))


def test_supported_public_bank_has_no_identical_view_with_conflicting_label() -> None:
    cases = _all_cases(MAX_BANK_SEEDS_V2)
    by_view: dict[str, SolverOutputV2] = {}
    for case in cases:
        digest = case.solver_view_digest(faulty=True)
        expected = case.expected_solver_output
        assert by_view.setdefault(digest, expected) == expected
    assert len(by_view) == len(cases) == 384


def test_every_case_has_one_typed_root_and_executable_clean_and_faulty_trajectories() -> None:
    for case in _all_cases(32):
        assert hard_v2._surface_arithmetic_valid_v2(case.semantic_program, faulty=False)
        assert hard_v2._surface_arithmetic_valid_v2(case.semantic_program, faulty=True)
        clean = execute_semantic_program_v2(case.semantic_program, faulty=False)
        faulty = execute_semantic_program_v2(case.semantic_program, faulty=True)
        assert clean.completed and clean.prefix_valid and clean.root_valid and clean.suffix_valid
        assert clean.invalid_lines == ()
        assert faulty.completed and faulty.prefix_valid and not faulty.root_valid
        assert faulty.suffix_valid
        assert faulty.invalid_lines == (case.root_line,)
        assert clean.final_state_digest != faulty.final_state_digest

        report = certification_report_v2(case)
        assert report.structural_passed
        assert report.deterministic_binding_passed
        assert report.artifact_binding_passed
        assert report.primary_verifier_passed
        assert report.independent_verifier_passed
        assert report.witness_passed
        assert report.root_fault_rejected
        assert report.semantic_clean_passed
        assert report.semantic_faulty_root_rejected
        assert report.semantic_clean_suffix_passed
        assert report.semantic_faulty_suffix_passed
        assert report.passed


def test_semantic_suffix_poisoning_is_rejected_at_program_construction() -> None:
    case = generate_development_case_v2(FamilyV2.GENERALIZED_CRT_PERIOD, 0)
    transition = case.semantic_program.suffix[-1]
    poisoned_declared = NormalizedMathValueV2.scalar(1)
    poisoned_expression = hard_v2._state("checksum")
    poisoned_surface = hard_v2._alias_expression_v2(
        poisoned_expression, case.semantic_program.surface_aliases
    )
    poisoned_target = case.semantic_program.surface_aliases["checksum"]
    poisoned = replace(
        transition,
        kind=hard_v2.TransitionKindV2.CHECK,
        expression=poisoned_expression,
        surface_expression=poisoned_surface,
        surface_target=poisoned_target,
        target=None,
        declared=poisoned_declared,
        display=hard_v2._transition_display_v2(
            hard_v2.TransitionKindV2.CHECK,
            poisoned_target,
            poisoned_surface,
            poisoned_declared,
        ),
    )
    with pytest.raises(ValueError, match="displayed basis arithmetic"):
        replace(
            case.semantic_program,
            suffix=(*case.semantic_program.suffix[:-1], poisoned),
        )


def test_program_renderer_rejects_unchecked_arbitrary_display_text() -> None:
    case = generate_development_case_v2(FamilyV2.NEGATIVE_EDGE_FINALIZATION, 0)
    with pytest.raises(ValueError, match="canonical rendering"):
        replace(case.semantic_program.suffix[-1], display="2+2=5")


def test_old_family_markers_and_first_token_locator_fail_on_8000_random_cases() -> None:
    markers = {
        FamilyV2.GENERALIZED_CRT_PERIOD: ("least positive common-solution period T=",),
        FamilyV2.BOUNDED_COMPOSITION_OVERLAP: ("For A1",),
        FamilyV2.NEGATIVE_EDGE_FINALIZATION: ("sets F=", "finalize it at F="),
        FamilyV2.FORCED_RECURRENCE_RESONANCE: ("Choose P_n=",),
    }
    old_first_tokens = {"Set", "For", "Since", "Choose"}
    randomizer = random.Random(20260714)
    marker_hits = 0
    token_hits = 0
    unique_body_hits = 0
    total = 8_000
    for index in range(total):
        family = FAMILY_ORDER_V2[index % len(FAMILY_ORDER_V2)]
        seed = randomizer.randrange(2**32)
        case = generate_development_case_v2(family, seed)
        lines = case.trace(faulty=True)
        marker_matches = [
            line_number
            for line_number, line in enumerate(lines, 1)
            if any(marker in line for marker in markers[family])
        ]
        if len(marker_matches) == 1 and marker_matches[0] == case.root_line:
            marker_hits += 1
        token_matches = [
            line_number
            for line_number, line in enumerate(lines, 1)
            if line.split(maxsplit=2)[1] in old_first_tokens
        ]
        if len(token_matches) == 1 and token_matches[0] == case.root_line:
            token_hits += 1
        candidate_shapes = {
            re.sub(r"-?[0-9]+(?:/[0-9]+)?", "#", lines[line - 1])
            for line in CALIBRATION_ELIGIBLE_FAULT_LINES_V2
        }
        if len(candidate_shapes) == len(CALIBRATION_ELIGIBLE_FAULT_LINES_V2):
            unique_body_hits += 1
    assert marker_hits < total
    assert token_hits < total
    assert unique_body_hits < total
    assert marker_hits == token_hits == unique_body_hits == 0


def test_root_scaffold_has_multiple_foils_and_no_high_precision_surface_token() -> None:
    cases = _all_cases(64)
    token_support: Counter[str] = Counter()
    token_root: Counter[str] = Counter()
    for case in cases:
        lines = case.trace(faulty=True)
        assert len(case.foil_lines) == 7
        candidate_transitions = case.semantic_program.transitions(faulty=True)[2:10]
        assert all(
            transition.kind is hard_v2.TransitionKindV2.ASSIGN_CHECKED
            for transition in candidate_transitions
        )
        target_aliases = {transition.surface_target for transition in candidate_transitions}
        assert len(target_aliases) == 8
        checksum_aliases = hard_v2._expression_state_names_v2(
            case.semantic_program.suffix[-2].surface_expression
        )
        assert target_aliases == checksum_aliases
        for line_number, line in enumerate(lines, 1):
            for token in set(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", line)):
                token_support[token] += 1
                if line_number == case.root_line:
                    token_root[token] += 1
    for token, support in token_support.items():
        if support >= 16:
            assert token_root[token] / support <= 0.5, token


def test_prompts_show_the_same_universal_schema_codes_and_no_fault_cue() -> None:
    forbidden = (
        "premature",
        "missing +1",
        "root line",
        "single_source_shortcut",
        "duplicated_correction",
    )
    cases = _all_cases(16)
    tags_by_answer_free_axes: dict[tuple[object, ...], str] = {}
    for case in cases:
        prompt = case.prompt
        answer_free_axes = (case.family, case.instance)
        tag = _public_case_tag(prompt)
        assert tags_by_answer_free_axes.setdefault(answer_free_axes, tag) == tag
        assert ANSWER_SCHEMA_V2 in prompt
        assert "invalid_transition" in prompt
        for code in UniversalWitnessCodeV2:
            assert code.value in prompt
        for kind in MathValueKindV2:
            assert kind.value in prompt
        for required_clause in (
            '"repair":{"target":"value","value":VALUE}',
            "solution_pair=[integer_vector(first solution,next solution)]",
            "feasible_assignment=[integer_vector(joint shifted-violation assignment),"
            "integer(correct bounded count)]",
            "directed_path=[symbol_vector(path vertices),integer(path length)]",
            "polynomial_identity=[rational(particular coefficient),"
            "integer_vector(second-difference coefficients),"
            "integer_vector(scaled residual coefficients),integer(faulty residual)]",
            'repair target is always the literal string "value"',
        ):
            assert required_clause in prompt
        assert all(fragment not in prompt.casefold() for fragment in forbidden)
        assert len(prompt.encode("utf-8")) <= MAX_PROMPT_BYTES_V2
    assert len(set(tags_by_answer_free_axes.values())) == len(tags_by_answer_free_axes)


def test_every_fault_is_false_from_public_prompt_semantics_and_target_is_linked() -> None:
    for case in _calibration_cases():
        legend = _public_alias_legend(case.prompt)
        faulty_trace = case.trace(faulty=True)
        public_aliases = {
            alias for line in faulty_trace for alias in re.findall(r"v-[0-9]{2}", line)
        }
        assert public_aliases <= set(legend)

        candidate_targets = tuple(
            _surface_claim(faulty_trace[line - 1])[0]
            for line in CALIBRATION_ELIGIBLE_FAULT_LINES_V2
        )
        assert len(set(candidate_targets)) == 8
        root_target, faulty_value, _ = _surface_claim(faulty_trace[case.faulty_line - 1])
        clean_target, clean_value, _ = _surface_claim(case.clean_trace[case.faulty_line - 1])
        visible_expected = _visible_expected_claim_value(case.prompt, root_target)
        assert clean_target == root_target
        assert clean_value == visible_expected
        assert faulty_value != visible_expected

        checksum_aliases = set(re.findall(r"v-[0-9]{2}", faulty_trace[10]))
        assert set(candidate_targets) <= checksum_aliases


def test_universal_normalized_solver_output_verifies_every_family() -> None:
    for case in _all_cases(16):
        expected = case.expected_solver_output
        assert expected.schema == ANSWER_SCHEMA_V2
        assert expected.error_code == "invalid_transition"
        assert expected.first_invalid_line == case.root_line
        assert case.verify_repair(expected.repair)[0]
        assert case.verify_witness(expected.witness)[0]
        assert case.verify_solver_output(expected)[0]
        assert case.verify_solver_output(expected.to_dict())[0]
        assert case.verify_solver_output(canonical_json_v2(expected.to_dict()))[0]
        assert not case.verify_repair(case.repair_replacement)[0]
        assert not case.verify_witness(case.witness.to_dict()["payload"])[0]


def test_universal_output_rejects_extra_keys_wrong_codes_and_noncanonical_values() -> None:
    case = generate_development_case_v2(FamilyV2.FORCED_RECURRENCE_RESONANCE, 0)
    expected = case.expected_solver_output.to_dict()

    extra = copy.deepcopy(expected)
    extra["trusted"] = True
    assert not case.verify_solver_output(extra)[0]

    wrong_code = copy.deepcopy(expected)
    wrong_code["witness"]["code"] = UniversalWitnessCodeV2.DIRECTED_PATH.value
    assert not case.verify_solver_output(wrong_code)[0]

    noncanonical = copy.deepcopy(expected)
    noncanonical["witness"]["values"][0]["payload"] = {
        "denominator": 16,
        "numerator": 2,
    }
    assert not case.verify_solver_output(noncanonical)[0]

    opaque = copy.deepcopy(expected)
    opaque["repair"] = {"replacement": case.repair_replacement}
    assert not case.verify_solver_output(opaque)[0]


def test_verifier_commitments_detect_live_checker_replacement(monkeypatch) -> None:
    case = generate_development_case_v2(FamilyV2.GENERALIZED_CRT_PERIOD, 0)
    commitment = case.witness_verifier_digest
    valid = case.expected_witness
    assert case.verify_witness(valid)[0]

    monkeypatch.setattr(hard_v2, "_verify_universal_witness_v2", lambda *_: True)
    passed, evidence = case.verify_witness(valid)
    assert not passed
    assert evidence != case.verify_witness(valid)[1] or evidence.startswith("sha256:")
    assert case.witness_verifier_digest == commitment
    assert not certification_report_v2(case).artifact_binding_passed


def test_verifier_commitments_detect_live_solver_json_parser_replacement(monkeypatch) -> None:
    case = generate_development_case_v2(FamilyV2.GENERALIZED_CRT_PERIOD, 0)
    assert not case.verify_solver_output("not-json")[0]

    monkeypatch.setattr(
        SolverOutputV2,
        "from_json",
        classmethod(lambda _cls, _raw: case.expected_solver_output),
    )

    assert not case.verify_solver_output("not-json")[0]
    assert not certification_report_v2(case).artifact_binding_passed


def test_verifier_commitments_detect_live_strict_json_loader_replacement(monkeypatch) -> None:
    case = generate_development_case_v2(FamilyV2.GENERALIZED_CRT_PERIOD, 0)
    assert not case.verify_solver_output("not-json")[0]

    monkeypatch.setattr(
        hard_v2,
        "strict_json_loads_v2",
        lambda _raw, *, max_bytes: case.expected_solver_output.to_dict(),
    )

    assert not case.verify_solver_output("not-json")[0]
    assert not certification_report_v2(case).artifact_binding_passed


def test_distinct_oracle_commitments_and_known_math_checks() -> None:
    for case in _all_cases(16):
        assert case.primary_verifier != case.independent_verifier
        assert case.primary_oracle_digest != case.independent_oracle_digest
        assert case.primary_oracle_digest != case.witness_oracle_digest
        assert case.independent_oracle_digest != case.witness_oracle_digest
        assert case.verifier_source_digest.startswith("sha256:")
        assert case.verifier_artifact_digest.startswith("sha256:")
        assert case.interpreter_schema == INTERPRETER_SCHEMA_V2
        assert case.repair_verifier_digest != case.witness_verifier_digest


def test_replay_is_bounded_and_fails_closed_without_consuming_arbitrary_iterables() -> None:
    case = generate_development_case_v2(FamilyV2.GENERALIZED_CRT_PERIOD, 0)

    class ExplosiveIterable:
        def __iter__(self):
            raise AssertionError("must not consume an arbitrary iterable")

    assert not case.replay(ExplosiveIterable()).accepted
    assert not case.replay((*case.clean_trace, "13. extra")).accepted
    poisoned = list(case.clean_trace)
    poisoned[0] = "1. \ud800"
    assert not case.replay(poisoned).accepted
    assert not case.verify_repair("\ud800")[0]
    assert not case.verify_witness("\ud800")[0]
    assert not case.verify_solver_output("\ud800")[0]
    assert not case.verify_solver_output("x" * 5_000)[0]


def test_strict_json_and_in_memory_budgets_reject_poisoning() -> None:
    with pytest.raises(ValueError, match="duplicate"):
        strict_json_loads_v2('{"x":1,"x":2}')
    with pytest.raises(ValueError, match="floating-point"):
        strict_json_loads_v2('{"x":1.0}')
    with pytest.raises(ValueError, match="non-finite"):
        strict_json_loads_v2('{"x":NaN}')

    case = generate_development_case_v2(FamilyV2.GENERALIZED_CRT_PERIOD, 0)
    oversized = {
        "target": "value",
        "value": {"kind": "integer_vector", "payload": {"items": list(range(65))}},
    }
    assert not case.verify_repair(oversized)[0]
    assert not case.verify_solver_output({"x": 1})[0]


def test_public_boundaries_reject_builtin_and_record_subclasses() -> None:
    class HostileText(str):
        encode_called = False

        def encode(self, *_args, **_kwargs):
            type(self).encode_called = True
            return b"{}"

    class HostileInt(int):
        pass

    class HostileList(list):
        pass

    class HostileRepair(RepairSubmissionV2):
        pass

    oversized = HostileText('{"x":"' + "a" * MAX_CASE_JSON_BYTES_V2 + '"}')
    with pytest.raises(ValueError, match="must be text"):
        strict_json_loads_v2(oversized)
    assert not HostileText.encode_called

    case = generate_development_case_v2(FamilyV2.GENERALIZED_CRT_PERIOD, 0)
    assert not case.verify_exact_repair(HostileText(case.repair_replacement))[0]
    assert not case.verify_solver_output(
        HostileText(canonical_json_v2(case.expected_solver_output.to_dict()))
    )[0]
    assert not HostileText.encode_called

    hostile_repair = HostileRepair(case.expected_repair.target, case.expected_repair.value)
    assert not case.verify_repair(hostile_repair)[0]

    raw_output = case.expected_solver_output.to_dict()
    raw_output[HostileText("schema")] = raw_output.pop("schema")
    with pytest.raises(ValueError, match=r"exact strings|string keys"):
        SolverOutputV2.from_dict(raw_output)
    raw_output = case.expected_solver_output.to_dict()
    raw_output["witness"]["values"] = HostileList(raw_output["witness"]["values"])
    with pytest.raises(ValueError, match="bounded array"):
        SolverOutputV2.from_dict(raw_output)
    with pytest.raises(ValueError, match="must be an integer"):
        encode_development_seed_v2(
            position=HostileInt(0),
            template=0,
            derivation_family=0,
            fault_variant=0,
            parameter_cell=0,
            instance=0,
        )


def test_unbounded_seed_iterable_stops_exactly_one_over_the_cap() -> None:
    class CountingForever:
        def __init__(self) -> None:
            self.count = 0

        def __iter__(self):
            return self

        def __next__(self) -> int:
            self.count += 1
            if self.count > MAX_BANK_SEEDS_V2 + 1:
                raise AssertionError("generator consumed past its one-over-cap sentinel")
            return self.count - 1

    source = CountingForever()
    with pytest.raises(ValueError, match="seed collection"):
        generate_public_development_bank_v2(source)
    assert source.count == MAX_BANK_SEEDS_V2 + 1


def test_canonical_round_trip_and_regeneration_reject_rehashed_mutations() -> None:
    for case in _all_cases(16):
        raw = case.canonical_json()
        assert len(raw.encode("utf-8")) <= MAX_CASE_JSON_BYTES_V2
        assert raw == canonical_json_v2(strict_json_loads_v2(raw))
        reloaded = HardChallengeCaseV2.from_json(raw)
        assert reloaded.digest() == case.digest()
        assert reloaded.content_payload() == case.content_payload()

    case = generate_development_case_v2(FamilyV2.GENERALIZED_CRT_PERIOD, 0)
    original = case.to_dict()
    for mutator in (
        lambda raw: raw.__setitem__("prompt", raw["prompt"] + " forged"),
        lambda raw: raw.__setitem__("domain", "forged domain"),
        lambda raw: raw.__setitem__("derivation_family", DERIVATION_FAMILY_ORDER_V2[1].value),
        lambda raw: raw.__setitem__("fault_variant", 1 - raw["fault_variant"]),
        lambda raw: raw.__setitem__("mechanism", "forged_mechanism"),
        lambda raw: raw.__setitem__("parameter_cell", 1 - raw["parameter_cell"]),
        lambda raw: raw.__setitem__("parameter_construction", PARAMETER_CELL_ORDER_V2[1].value),
        lambda raw: raw.__setitem__("generator_cell", "public-hard-v2/parameter-constructive"),
        lambda raw: raw.__setitem__("repair_description", "forged repair"),
        lambda raw: raw["parameters"].__setitem__("x0", raw["parameters"]["x0"] + 1),
        lambda raw: raw["semantic_program"]["surface_aliases"].__setitem__("claim0", "v99"),
        lambda raw: raw["semantic_program"]["suffix"][-1].__setitem__("display", "2+2=5"),
    ):
        poisoned = copy.deepcopy(original)
        mutator(poisoned)
        with pytest.raises(ValueError):
            HardChallengeCaseV2.from_dict(_rehash(poisoned))


def test_solver_views_are_shared_metadata_free_payloads() -> None:
    for case in _all_cases(4):
        clean = case.clean_solver_view
        faulty = case.faulty_solver_view
        assert type(clean) is SolverView
        assert type(faulty) is SolverView
        assert clean.prompt == faulty.prompt == case.prompt
        assert clean.numbered_solution == case.clean_trace
        assert faulty.numbered_solution == case.trace(faulty=True)
        assert set(clean.to_dict()) == {"numbered_solution", "prompt", "schema", "schema_version"}
        rendered = clean.canonical_json() + faulty.canonical_json()
        for hidden in (
            case.case_id,
            case.family.value,
            case.mechanism,
            case.template_id,
            case.verifier_artifact_digest,
            case.witness.kind.value,
        ):
            assert hidden not in rendered


def test_symbolic_recurrence_identity_not_finite_prefix_sampling() -> None:
    assert hard_v2._second_difference_coefficients_v2(1) == (0,)
    assert hard_v2._second_difference_coefficients_v2(2) == (2,)
    assert hard_v2._second_difference_coefficients_v2(3) == (6, 6)
    for seed in range(96):
        case = generate_development_case_v2(FamilyV2.FORCED_RECURRENCE_RESONANCE, seed)
        coefficient = Fraction(
            case.parameters["coefficient_numerator"],
            case.parameters["coefficient_denominator"],
        )
        assert coefficient * 2 * case.parameters["r"] ** 2 == 1
        assert hard_v2._recurrence_primary_v2(case)
        assert hard_v2._recurrence_independent_v2(case)


def test_family_math_witnesses_remain_exact() -> None:
    for seed in range(32):
        crt = generate_development_case_v2(FamilyV2.GENERALIZED_CRT_PERIOD, seed)
        p = crt.parameters
        first, second = crt.expected_witness.values[0].as_integer_vector()
        assert second - first == p["true_period"]
        assert all(
            value % modulus == residue
            for value in (first, second)
            for modulus, residue in ((p["m1"], p["r1"]), (p["m2"], p["r2"]))
        )

        composition = generate_development_case_v2(FamilyV2.BOUNDED_COMPOSITION_OVERLAP, seed)
        p = composition.parameters
        solutions = [
            (x1, x2, p["total"] - x1 - x2)
            for x1 in range(p["b1"] + 1)
            for x2 in range(p["b2"] + 1)
            if 0 <= p["total"] - x1 - x2 <= p["b3"]
        ]
        assert len(solutions) == p["correct_count"]

        path = generate_development_case_v2(FamilyV2.NEGATIVE_EDGE_FINALIZATION, seed)
        p = path.parameters
        faulty_a = (p["sa"], p["second_pass_a"])[path.fault_variant]
        faulty_t = faulty_a + p["at"]
        assert p["sb"] + p["ba"] == p["true_a"] < faulty_a
        assert p["true_a"] + p["at"] == p["true_t"] < faulty_t


def test_bank_supports_256_and_384_case_rectangles() -> None:
    default = generate_public_development_bank_v2()
    expanded = generate_public_development_bank_v2(range(96))
    assert len(default) == 64 * 4 == 256
    assert len(expanded) == 96 * 4 == 384
    assert MAX_BANK_SEEDS_V2 == 96
    assert len({case.case_id for case in expanded}) == len(expanded)
    with pytest.raises(ValueError, match="between 1 and 96"):
        generate_public_development_bank_v2(range(97))
    with pytest.raises(ValueError, match="unique"):
        generate_public_development_bank_v2((1, 1))
    with pytest.raises(ValueError, match="iterable"):
        generate_public_development_bank_v2("12")


def test_typed_value_and_witness_payload_validation_remain_strict() -> None:
    with pytest.raises(ValueError, match="reduced"):
        NormalizedMathValueV2(
            MathValueKindV2.RATIONAL,
            {"denominator": 16, "numerator": 2},
        )
    with pytest.raises(ValueError, match="simple identifiers"):
        NormalizedMathValueV2.symbol_vector(("s", "b->a"))
    with pytest.raises(ValueError, match=r"unknown|invalid|keys"):
        SolverOutputV2.from_dict({"schema": ANSWER_SCHEMA_V2})

    legacy = generate_development_case_v2(FamilyV2.FORCED_RECURRENCE_RESONANCE, 0)
    with pytest.raises(ValueError, match="invalid recurrence witness payload keys"):
        DiagnosticWitnessV2(
            WitnessKindV2.RESONANCE_RESIDUAL,
            {**legacy.witness.to_dict()["payload"], "trusted": 1},
        )
