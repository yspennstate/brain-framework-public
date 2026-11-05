import hashlib
import json
import re
import time
from collections import Counter, defaultdict
from collections.abc import Callable, Hashable, Iterable
from dataclasses import FrozenInstanceError, replace
from fractions import Fraction
from math import comb, gcd, isqrt, log
from pathlib import Path

import pytest

import math_brain.benchmark as benchmark_module
from math_brain.benchmark import (
    FAMILY_ORDER,
    FAMILY_SPECS,
    FAULT_ORDER,
    GENERATOR_BUNDLE_HASH,
    GENERATOR_SOURCE_HASH,
    GENERATOR_VERSION,
    MANIFEST_SCHEMA_VERSION,
    MATHIR_SCHEMA_VERSION,
    MAX_EXPRESSION_DEPTH,
    MAX_FRACTION_BITS,
    MAX_ITEM_JSON_BYTES,
    MAX_MANIFEST_ITEMS,
    MAX_RNG_BOUND_BITS,
    MAX_SOLVER_VIEW_JSON_BYTES,
    MAX_SOLUTION_STEPS,
    MAX_STRING_BYTES,
    PRIVATE_DATA_LICENSE,
    SOLVER_VALID_FAULTS,
    BenchmarkItem,
    BenchmarkManifest,
    CounterRNG,
    Expr,
    ExprOp,
    FaultKind,
    Provenance,
    Relation,
    Solution,
    SolverAssignment,
    SolverView,
    build_manifest,
    differing_steps,
    evaluate_expression,
    generate_bank,
    generate_item,
    generate_manifest,
    replay_repair,
    render_relation,
    validate_solution,
    verify_manifest,
)
from math_brain.benchmark_verifier import verify_family as verify_family_independently


def _canonical_digest(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _rebind_content_digest(raw: dict[str, object]) -> None:
    payload = {key: value for key, value in raw.items() if key != "content_digest"}
    raw["content_digest"] = _canonical_digest(payload)


_GIVEN_KEYS = {
    "arithmetic_progression": {"a", "d", "n"},
    "base_conversion": {"base", "number"},
    "crt_two_moduli": {"m1", "m2", "r1", "r2"},
    "determinant_3x3": {f"a{row}{column}" for row in range(1, 4) for column in range(1, 4)},
    "gcd_euclidean": {"a", "b"},
    "geometric_progression": {"a", "n", "r"},
    "horner_polynomial": {"a0", "a1", "a2", "a3", "x"},
    "hypergeometric_probability": {"bad", "draws", "good", "successes"},
    "linear_equation": {"a", "b", "c"},
    "modular_inverse": {"a", "m"},
    "monomial_integral": {"b", "c", "p"},
    "polynomial_derivative": {"a1", "a2", "a3", "x"},
    "pythagorean_hypotenuse": {"a", "b"},
    "quadratic_integer_roots": {"b", "c"},
    "recurrence": {"n", "p", "q", "u0"},
    "subset_count": {"k", "n"},
    "triangle_area": {"base", "height"},
    "two_by_two_system": {"a", "b", "c", "d", "e", "f"},
}


def _independent_base(number: int, base: int) -> str:
    alphabet = "0123456789ABCDEF"
    digits: list[str] = []
    while number:
        number, digit = divmod(number, base)
        digits.append(alphabet[digit])
    return "".join(reversed(digits)) or "0"


def _independent_answer(family: str, p: dict[str, int]) -> str:
    if family == "arithmetic_progression":
        return f"S = {p['n'] * (2 * p['a'] + (p['n'] - 1) * p['d']) // 2}"
    if family == "base_conversion":
        return f"digits = {_independent_base(p['number'], p['base'])}"
    if family == "crt_two_moduli":
        solution = next(
            value
            for value in range(p["m1"] * p["m2"])
            if value % p["m1"] == p["r1"] and value % p["m2"] == p["r2"]
        )
        return f"x = {solution}"
    if family == "determinant_3x3":
        a, b, c = (p[name] for name in ("a11", "a12", "a13"))
        d, e, f = (p[name] for name in ("a21", "a22", "a23"))
        g, h, i = (p[name] for name in ("a31", "a32", "a33"))
        value = a * (e * i - f * h) - b * (d * i - f * g) + c * (d * h - e * g)
        return f"det = {value}"
    if family == "gcd_euclidean":
        return f"gcd = {gcd(p['a'], p['b'])}"
    if family == "geometric_progression":
        total = p["a"] * (p["r"] ** p["n"] - 1) // (p["r"] - 1)
        return f"S = {total}"
    if family == "horner_polynomial":
        value = ((p["a3"] * p["x"] + p["a2"]) * p["x"] + p["a1"]) * p["x"] + p["a0"]
        return f"P({p['x']}) = {value}"
    if family == "hypergeometric_probability":
        probability = Fraction(
            comb(p["good"], p["successes"])
            * comb(p["bad"], p["draws"] - p["successes"]),
            comb(p["good"] + p["bad"], p["draws"]),
        )
        return f"p = {probability.numerator}/{probability.denominator}"
    if family == "linear_equation":
        return f"x = {(p['c'] - p['b']) // p['a']}"
    if family == "modular_inverse":
        return f"inverse = {pow(p['a'], -1, p['m'])}"
    if family == "monomial_integral":
        value = p["c"] * p["b"] ** (p["p"] + 1) // (p["p"] + 1)
        return f"integral = {value}"
    if family == "polynomial_derivative":
        value = 3 * p["a3"] * p["x"] ** 2 + 2 * p["a2"] * p["x"] + p["a1"]
        return f"P'({p['x']}) = {value}"
    if family == "pythagorean_hypotenuse":
        return f"h = {isqrt(p['a'] ** 2 + p['b'] ** 2)}"
    if family == "quadratic_integer_roots":
        root = (-p["b"] + isqrt(p["b"] ** 2 - 4 * p["c"])) // 2
        return f"x = {root}"
    if family == "recurrence":
        value = p["u0"]
        for _ in range(p["n"]):
            value = p["p"] * value + p["q"]
        return f"u_{p['n']} = {value}"
    if family == "subset_count":
        return f"N = {comb(p['n'], p['k'])}"
    if family == "triangle_area":
        return f"area = {p['base'] * p['height'] // 2}"
    if family == "two_by_two_system":
        determinant = p["a"] * p["d"] - p["b"] * p["c"]
        return f"x = {(p['e'] * p['d'] - p['b'] * p['f']) // determinant}"
    raise AssertionError(f"missing independent oracle for {family}")


def _masked_solution_template(item: BenchmarkItem, *, use_faulty: bool) -> tuple[str, ...]:
    view = item.solver_view(use_faulty=use_faulty)
    return tuple(re.sub(r"[-+]?\d+", "#", line) for line in view.numbered_solution)


def _template_rows(
    items: Iterable[BenchmarkItem],
    *,
    fault_kinds: tuple[FaultKind, ...] = FAULT_ORDER,
) -> list[tuple[tuple[str, ...], int]]:
    rows: list[tuple[tuple[str, ...], int]] = []
    for item in items:
        if item.fault.kind not in fault_kinds:
            continue
        rows.extend(
            (
                (_masked_solution_template(item, use_faulty=False), 0),
                (_masked_solution_template(item, use_faulty=True), 1),
            )
        )
    return rows


def _numeric_summary_rows(
    items: Iterable[BenchmarkItem],
    *,
    fault_kinds: tuple[FaultKind, ...],
) -> list[tuple[tuple[int, int, int, int], int]]:
    rows: list[tuple[tuple[int, int, int, int], int]] = []
    pattern = re.compile(r"(?<![A-Za-z_0-9])[-+]?\d+(?![A-Za-z_0-9])")
    for item in items:
        if item.fault.kind not in fault_kinds:
            continue
        for use_faulty, label in ((False, 0), (True, 1)):
            text = " ".join(item.solver_view(use_faulty=use_faulty).numbered_solution)
            numbers = [int(value) for value in pattern.findall(text)]
            rows.append(
                (
                    (
                        len(numbers),
                        sum(value < 0 for value in numbers),
                        sum(value == 0 for value in numbers),
                        sum(value == 1 for value in numbers),
                    ),
                    label,
                )
            )
    return rows


def _heldout_feature_accuracy(
    training: list[tuple[Hashable, int]],
    heldout: list[tuple[Hashable, int]],
) -> float:
    labels_by_feature: dict[Hashable, Counter[int]] = defaultdict(Counter)
    for feature, label in training:
        labels_by_feature[feature][label] += 1
    correct = 0
    for feature, label in heldout:
        counts = labels_by_feature.get(feature)
        prediction = 1 if counts is not None and counts[1] > counts[0] else 0
        correct += prediction == label
    return correct / len(heldout)


def _solver_text_rows(
    items: Iterable[BenchmarkItem],
    *,
    fault_kinds: tuple[FaultKind, ...],
) -> list[tuple[str, int]]:
    rows: list[tuple[str, int]] = []
    for item in items:
        if item.fault.kind not in fault_kinds:
            continue
        for use_faulty, label in ((False, 0), (True, 1)):
            view = item.solver_view(use_faulty=use_faulty)
            rows.append((view.prompt + "\n" + "\n".join(view.numbered_solution), label))
    return rows


def _heldout_character_trigram_accuracy(
    training: list[tuple[str, int]],
    heldout: list[tuple[str, int]],
) -> float:
    counts = [Counter[str](), Counter[str]()]
    totals = [0, 0]
    vocabulary: set[str] = set()
    for text, label in training:
        grams = Counter(text[index : index + 3] for index in range(len(text) - 2))
        counts[label].update(grams)
        totals[label] += sum(grams.values())
        vocabulary.update(grams)
    correct = 0
    for text, label in heldout:
        grams = Counter(text[index : index + 3] for index in range(len(text) - 2))
        scores = []
        for candidate_label in (0, 1):
            denominator = totals[candidate_label] + len(vocabulary)
            scores.append(
                sum(
                    frequency
                    * log((counts[candidate_label][gram] + 1) / denominator)
                    for gram, frequency in grams.items()
                )
            )
        prediction = 1 if scores[1] > scores[0] else 0
        correct += prediction == label
    return correct / len(heldout)


def _best_grouped_accuracy(rows: list[tuple[str, int]]) -> float:
    labels_by_feature: dict[str, Counter[int]] = defaultdict(Counter)
    for feature, label in rows:
        labels_by_feature[feature][label] += 1
    return sum(max(counts.values()) for counts in labels_by_feature.values()) / len(rows)


def test_counter_rng_has_a_pinned_cross_run_vector() -> None:
    rng = CounterRNG("known-answer-seed", stream="vector")
    assert [rng.randbelow(10**9) for _ in range(6)] == [
        551417865,
        607109824,
        658838001,
        499237838,
        911980598,
        126256825,
    ]


def test_counter_rng_rejects_invalid_ranges_and_empty_seed() -> None:
    with pytest.raises(ValueError, match="seed"):
        CounterRNG("")
    rng = CounterRNG("valid")
    with pytest.raises(ValueError, match="positive"):
        rng.randbelow(0)
    with pytest.raises(ValueError, match="lower"):
        rng.randint(2, 1)
    with pytest.raises(ValueError, match="empty"):
        rng.choice(())


def test_counter_rng_handles_bounds_larger_than_one_sha256_block() -> None:
    upper = (1 << (MAX_RNG_BOUND_BITS - 1)) + 12345
    value = CounterRNG("wide-bound").randbelow(upper)
    assert 0 <= value < upper
    with pytest.raises(ValueError, match="counter-RNG width budget"):
        CounterRNG("too-wide").randbelow(1 << MAX_RNG_BOUND_BITS)


def test_random_access_generation_matches_sequential_generation() -> None:
    direct = generate_item("addressable-bank", 97)
    sequential = list(generate_bank("addressable-bank", 98))[-1]
    assert direct == sequential
    assert direct.item_id == sequential.item_id


def test_random_access_coordinates_cannot_escape_the_fixed_bank() -> None:
    with pytest.raises(ValueError, match=r"\[0, 10000\)"):
        generate_item("bounded-bank", 10_000)
    with pytest.raises(ValueError, match="fit the 10000-item bank"):
        list(generate_bank("bounded-bank", 2, start=9_999))


def test_all_eighteen_families_use_givens_only_and_match_independent_oracles() -> None:
    items = list(generate_bank("independent-family-oracles", len(FAMILY_ORDER)))
    assert len(FAMILY_ORDER) == len(FAMILY_SPECS) == 18
    assert {item.problem.family for item in items} == set(FAMILY_ORDER)
    for item in items:
        parameters = item.problem.parameter_map
        assert set(parameters) == _GIVEN_KEYS[item.problem.family]
        assert item.problem.answer == _independent_answer(item.problem.family, parameters)
        assert item.problem.semantic_fingerprint == _canonical_digest(
            {
                "family": item.problem.family,
                "givens": parameters,
                "schema": "org.math-research-brain/canonical-givens",
                "schema_version": 1,
            }
        )
        assert FAMILY_SPECS[item.problem.family].domain_size >= 556


def test_generator_and_separate_verifier_match_at_every_family_domain_edge() -> None:
    for family, specification in FAMILY_SPECS.items():
        ranks = {0, specification.domain_size // 2, specification.domain_size - 1}
        for rank in ranks:
            givens = dict(specification.unrank(rank))
            generated = specification.oracle(givens)
            independent = verify_family_independently(family, givens)
            independent_relations = benchmark_module._relations_from_independent(
                independent.relations
            )
            assert (
                generated.prompt,
                generated.answer,
                generated.difficulty.value,
                generated.witness,
                generated.relations,
            ) == (
                independent.prompt,
                independent.answer,
                independent.difficulty,
                independent.witness,
                independent_relations,
            )
            clean = benchmark_module._solution(generated.relations, generated.answer)
            candidates = benchmark_module._enumerate_mutations(
                clean,
                dict(generated.witness),
            )
            assert {candidate.kind for candidate in candidates} == set(FAULT_ORDER)


@pytest.mark.parametrize(
    ("family", "givens"),
    [
        ("linear_equation", {"a": 1, "b": 2, "c": 5}),
        ("pythagorean_hypotenuse", {"a": 27, "b": 36}),
        (
            "determinant_3x3",
            {
                "a11": 7,
                "a12": 0,
                "a13": 0,
                "a21": 0,
                "a22": 1,
                "a23": 0,
                "a31": 0,
                "a32": 0,
                "a33": 1,
            },
        ),
        ("monomial_integral", {"b": 1, "c": 42, "p": 1}),
    ],
)
def test_independent_verifier_enforces_the_generator_domain(
    family: str,
    givens: dict[str, int],
) -> None:
    with pytest.raises(ValueError, match="verifier domain"):
        verify_family_independently(family, givens)


def test_item_has_pinned_identity_and_content_digest() -> None:
    item = generate_item("known-answer-seed", 17)
    assert item.generation.generator_version == GENERATOR_VERSION == "synthetic-v4-independent"
    assert item.item_id.startswith("mathir_v4_")
    assert item.item_id != (
        "mathir_788bf38b4c4cfa4f502b3d56f24fb2e82abccac2b796b37d66fa4c3b2c108a50"
    )
    assert item.item_id == (
        "mathir_v4_b26ec3905c12b70cd60566e66e617c37a64141d10b3c3903787d2d29647ca970"
    )
    assert item.content_digest == _canonical_digest(item.content_payload())
    assert item.digest() == _canonical_digest(item.to_dict())


def test_mathir_json_round_trip_is_byte_canonical() -> None:
    original = generate_item("round-trip", 8)
    assert original.schema_version == MATHIR_SCHEMA_VERSION == 4
    restored = BenchmarkItem.from_json(original.canonical_json())
    assert restored == original
    assert restored.canonical_json() == original.canonical_json()
    assert restored.digest() == original.digest()


def test_coordinate_replay_uses_one_nonrecursive_unchecked_build(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    item = generate_item("nonrecursive-replay", 3)
    original = benchmark_module._build_item_from_coordinates
    calls = 0

    def counted_build(seed: str, index: int) -> BenchmarkItem:
        nonlocal calls
        calls += 1
        return original(seed, index)

    monkeypatch.setattr(
        benchmark_module,
        "_build_item_from_coordinates",
        counted_build,
    )
    assert BenchmarkItem.from_json(item.canonical_json()) == item
    assert calls == 1


def test_solver_view_is_minimal_immutable_and_blinded_for_both_variants() -> None:
    item = generate_item("solver-view", 11)
    clean = item.solver_view(use_faulty=False)
    faulty = item.solver_view(use_faulty=True)

    assert isinstance(clean, SolverView)
    assert clean.prompt == faulty.prompt == item.problem.prompt
    assert sum(a != b for a, b in zip(clean.numbered_solution, faulty.numbered_solution)) == 1
    for view in (clean, faulty):
        payload = view.to_dict()
        assert set(payload) == {"numbered_solution", "prompt", "schema", "schema_version"}
        assert not {
            "answer",
            "witness",
            "parameters",
            "fault",
            "repair",
            "variant",
            "is_faulty",
            "clean",
            "faulty",
        } & set(payload)
        assert SolverView.from_json(view.canonical_json()) == view
    with pytest.raises(FrozenInstanceError):
        clean.prompt = "mutated"  # type: ignore[misc]


def test_solver_assignment_uses_run_local_ids_and_hides_stable_metadata() -> None:
    item = generate_item("opaque-assignment", 11)
    first_nonce = b"a" * 32
    clean = item.solver_assignment(first_nonce, use_faulty=False)
    faulty = item.solver_assignment(first_nonce, use_faulty=True)
    other_run = item.solver_assignment(b"b" * 32, use_faulty=True)

    assert isinstance(clean, SolverAssignment)
    assert clean.solver_id == faulty.solver_id
    assert clean.solver_id != other_run.solver_id
    serialized = json.dumps(clean.to_dict(), sort_keys=True)
    assert item.item_id not in serialized
    assert item.content_digest not in serialized
    assert "mathir_v4_" not in serialized
    assert "sha256:" not in serialized
    assert GENERATOR_VERSION not in serialized
    with pytest.raises(ValueError, match="run nonce"):
        item.solver_assignment(b"short", use_faulty=False)


def test_certificates_have_no_universal_canned_line() -> None:
    items = list(generate_bank("family-line-canary", len(FAMILY_ORDER)))
    assert {item.problem.family for item in items} == set(FAMILY_ORDER)
    masked_line_sets = [
        set(_masked_solution_template(item, use_faulty=False))
        for item in items
    ]
    assert not set.intersection(*masked_line_sets)


def test_shortcut_canaries_use_disjoint_seeds_and_demote_leaky_faults() -> None:
    training_items = list(generate_bank("shortcut-train", 288))
    heldout_items = list(generate_bank("shortcut-holdout", 288))
    fuzz_faults = tuple(kind for kind in FAULT_ORDER if kind not in SOLVER_VALID_FAULTS)

    all_accuracy = _heldout_feature_accuracy(
        _template_rows(training_items),
        _template_rows(heldout_items),
    )
    solver_valid_template_accuracy = _heldout_feature_accuracy(
        _template_rows(training_items, fault_kinds=SOLVER_VALID_FAULTS),
        _template_rows(heldout_items, fault_kinds=SOLVER_VALID_FAULTS),
    )
    fuzz_template_accuracy = _heldout_feature_accuracy(
        _template_rows(training_items, fault_kinds=fuzz_faults),
        _template_rows(heldout_items, fault_kinds=fuzz_faults),
    )
    solver_valid_numeric_accuracy = _heldout_feature_accuracy(
        _numeric_summary_rows(training_items, fault_kinds=SOLVER_VALID_FAULTS),
        _numeric_summary_rows(heldout_items, fault_kinds=SOLVER_VALID_FAULTS),
    )
    sign_numeric_accuracy = _heldout_feature_accuracy(
        _numeric_summary_rows(training_items, fault_kinds=(FaultKind.SIGN_ERROR,)),
        _numeric_summary_rows(heldout_items, fault_kinds=(FaultKind.SIGN_ERROR,)),
    )
    solver_valid_trigram_accuracy = _heldout_character_trigram_accuracy(
        _solver_text_rows(training_items, fault_kinds=SOLVER_VALID_FAULTS),
        _solver_text_rows(heldout_items, fault_kinds=SOLVER_VALID_FAULTS),
    )

    # Removing the prompt leaves only number-masked expression templates.  The held-out
    # baseline stays far from perfect overall and at chance on the solver-valid strata.
    assert all_accuracy <= 0.70
    assert solver_valid_template_accuracy <= 0.60
    assert solver_valid_numeric_accuracy <= 0.60
    assert solver_valid_trigram_accuracy <= 0.60
    assert fuzz_template_accuracy > 0.60
    assert sign_numeric_accuracy > 0.75
    assert SOLVER_VALID_FAULTS == (FaultKind.OFF_BY_ONE,)

    # Every numbered line is family-specific in v4.  Removing those lines leaves the paired
    # prompt, while run-local IDs pair the same case; neither feature can reveal the label.
    prompt_only: list[tuple[str, int]] = []
    id_only: list[tuple[str, int]] = []
    line_count_only: list[tuple[str, int]] = []
    run_nonce = b"shortcut-canary-run-nonce-0001"
    for item in heldout_items:
        prompt_only.extend(((item.problem.prompt, 0), (item.problem.prompt, 1)))
        solver_id = item.solver_assignment(run_nonce, use_faulty=False).solver_id
        id_only.extend(((solver_id, 0), (solver_id, 1)))
        line_count = str(len(item.clean_solution.steps))
        line_count_only.extend(((line_count, 0), (line_count, 1)))
    assert _best_grouped_accuracy(prompt_only) == 0.5
    assert _best_grouped_accuracy(id_only) == 0.5
    assert _best_grouped_accuracy(line_count_only) == 0.5


def test_solver_view_near_aggregate_budget_round_trips_and_overflow_fails() -> None:
    prompt = "p" * 15_000
    lines = tuple(f"{number}. " + "x" * 15_000 for number in range(1, 8))
    view = SolverView(prompt=prompt, numbered_solution=lines)
    encoded = view.canonical_json().encode("utf-8")
    assert len(encoded) > MAX_SOLVER_VIEW_JSON_BYTES * 9 // 10
    assert len(encoded) <= MAX_SOLVER_VIEW_JSON_BYTES
    assert SolverView.from_json(view.canonical_json()) == view

    oversized = tuple(f"{number}. " + "x" * 15_000 for number in range(1, 9))
    with pytest.raises(ValueError, match="aggregate budget"):
        SolverView(prompt=prompt, numbered_solution=oversized)


@pytest.mark.parametrize("field", ["prompt", "answer"])
def test_certification_binds_problem_statement_fields_to_family_oracle(field: str) -> None:
    raw = generate_item("problem-binding", 7).to_dict()
    raw["problem"][field] += " forged"
    _rebind_content_digest(raw)
    with pytest.raises(ValueError, match=f"problem binding failed.*{field}"):
        BenchmarkItem.from_dict(raw)


def test_certification_binds_solution_conclusion_to_derived_answer() -> None:
    raw = generate_item("conclusion-binding", 12).to_dict()
    for variant in ("clean_solution", "faulty_solution"):
        raw[variant]["conclusion"] = "forged conclusion"
    raw["repair"]["expected_faulty_digest"] = _canonical_digest(raw["faulty_solution"])
    raw["repair"]["expected_repaired_digest"] = _canonical_digest(raw["clean_solution"])
    _rebind_content_digest(raw)
    with pytest.raises(ValueError, match="problem binding failed.*solution conclusion"):
        BenchmarkItem.from_dict(raw)


def test_separate_verifier_rejects_a_poisoned_generator_oracle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seed, index = "poisoned-generator", 0
    family = benchmark_module._scheduled_family(seed, index)[1]
    specification = FAMILY_SPECS[family]

    def poisoned(parameters: dict[str, int]) -> object:
        expectation = specification.oracle(parameters)
        return replace(expectation, prompt=expectation.prompt + " forged")

    monkeypatch.setitem(
        FAMILY_SPECS,
        family,
        replace(specification, oracle=poisoned),
    )
    with pytest.raises(ValueError, match="problem binding failed.*prompt"):
        generate_item(seed, index)


def test_deterministic_replay_rejects_same_family_coordinate_transplant() -> None:
    target = generate_item("coordinate-transplant", 0)
    source = None
    for index in range(1, 100):
        candidate = generate_item("coordinate-transplant", index)
        if candidate.problem.family == target.problem.family:
            source = candidate
            break
    assert source is not None
    raw = target.to_dict()
    source_raw = source.to_dict()
    for field in ("problem", "clean_solution", "faulty_solution", "fault", "repair"):
        raw[field] = source_raw[field]
    _rebind_content_digest(raw)

    with pytest.raises(ValueError, match="deterministic generation replay mismatch") as captured:
        BenchmarkItem.from_dict(raw)
    for field in ("problem", "clean solution", "faulty solution", "repair"):
        assert field in str(captured.value)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("kind", "wrong_operation", "mutation does not match"),
        (
            "description",
            "self-asserted relabel without deterministic regeneration",
            "deterministic generation replay mismatch: fault label",
        ),
    ],
)
def test_deterministic_replay_rejects_fault_relabels(
    field: str,
    value: str,
    message: str,
) -> None:
    raw = generate_item("fault-relabel", 4).to_dict()
    raw["fault"][field] = value
    _rebind_content_digest(raw)
    with pytest.raises(ValueError, match=message):
        BenchmarkItem.from_dict(raw)


def test_semantic_fingerprint_is_recomputed_from_canonical_givens() -> None:
    raw = generate_item("semantic-binding", 5).to_dict()
    raw["problem"]["semantic_fingerprint"] = "sha256:" + "0" * 64
    _rebind_content_digest(raw)
    with pytest.raises(ValueError, match="semantic fingerprint does not match"):
        BenchmarkItem.from_dict(raw)


def test_content_digest_detects_identity_preserving_prompt_mutation() -> None:
    item = generate_item("content-addressing", 4)
    raw = item.to_dict()
    raw["problem"]["prompt"] += " tampered"
    assert raw["item_id"] == item.item_id
    with pytest.raises(ValueError, match="content digest"):
        BenchmarkItem.from_dict(raw)


def test_generated_provenance_is_structured_and_hashes_normalized_source() -> None:
    item = generate_item("provenance", 1)
    provenance = item.provenance
    source = Path(__file__).parents[1] / provenance.generator_source
    normalized = source.read_text(encoding="utf-8").replace("\r\n", "\n").replace("\r", "\n")
    expected_hash = "sha256:" + hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    bundle_payload = bytearray(b"math-research-brain/generator-bundle/v1\x00")
    for name in ("benchmark.py", "benchmark_verifier.py"):
        bundle_source = (source.parent / name).read_text(encoding="utf-8")
        bundle_source = bundle_source.replace("\r\n", "\n").replace("\r", "\n")
        encoded_name = name.encode("utf-8")
        encoded_source = bundle_source.encode("utf-8")
        bundle_payload.extend(len(encoded_name).to_bytes(4, "big"))
        bundle_payload.extend(encoded_name)
        bundle_payload.extend(len(encoded_source).to_bytes(8, "big"))
        bundle_payload.extend(encoded_source)
    expected_bundle_hash = "sha256:" + hashlib.sha256(bundle_payload).hexdigest()
    assert provenance == Provenance()
    assert provenance.license_expression == PRIVATE_DATA_LICENSE
    assert provenance.generator_source_hash == GENERATOR_SOURCE_HASH == expected_hash
    assert provenance.generator_bundle_hash == GENERATOR_BUNDLE_HASH == expected_bundle_hash

    with pytest.raises(ValueError, match="noncanonical private benchmark license"):
        Provenance(license_expression="LicenseRef-Self-Asserted")
    with pytest.raises(ValueError, match="current trusted source"):
        Provenance(generator_source_hash="sha256:" + "0" * 64)
    with pytest.raises(ValueError, match="trusted verifier bundle"):
        Provenance(generator_bundle_hash="sha256:" + "0" * 64)


@pytest.mark.parametrize("version", [1, 2, 3])
def test_legacy_item_compatibility_is_explicitly_rejected(version: int) -> None:
    raw = generate_item("legacy-item", 0).to_dict()
    raw["schema_version"] = version
    raw.pop("content_digest")
    with pytest.raises(ValueError, match="legacy MathIR records"):
        BenchmarkItem.from_dict(raw)


def test_mathir_parser_rejects_unknown_fields_and_non_integer_constants() -> None:
    raw = generate_item("strict-schema", 2).to_dict()
    raw["unexpected"] = "silently accepting this would make hashes ambiguous"
    with pytest.raises(ValueError, match="extra"):
        BenchmarkItem.from_dict(raw)

    expression = {"op": "const", "value": True}
    with pytest.raises(ValueError, match="integer"):
        Expr.from_dict(expression)


def test_json_parsers_reject_duplicate_keys_before_schema_processing() -> None:
    item = generate_item("duplicate-json", 0)
    duplicate_item = item.canonical_json().replace("{", '{"schema":"shadow",', 1)
    with pytest.raises(ValueError, match="duplicate JSON object key.*schema"):
        BenchmarkItem.from_json(duplicate_item)

    manifest = generate_manifest("duplicate-manifest", 3)
    duplicate_manifest = manifest.canonical_json().replace("{", '{"seed":"shadow",', 1)
    with pytest.raises(ValueError, match="duplicate JSON object key.*seed"):
        BenchmarkManifest.from_json(duplicate_manifest)


def test_item_json_parser_enforces_raw_byte_and_nesting_budgets() -> None:
    with pytest.raises(ValueError, match="byte budget"):
        BenchmarkItem.from_json("x" * (MAX_ITEM_JSON_BYTES + 1))
    with pytest.raises(ValueError, match="byte budget"):
        BenchmarkItem.from_json("🙂" * (MAX_ITEM_JSON_BYTES // 4 + 1))
    deeply_nested = "[" * 2_000 + "0" + "]" * 2_000
    with pytest.raises(ValueError, match="nesting budget|benchmark item must be an object"):
        BenchmarkItem.from_json(deeply_nested)
    with pytest.raises(ValueError, match="benchmark item must be an object"):
        BenchmarkItem.from_json("[]")


@pytest.mark.parametrize(
    "reader",
    [BenchmarkItem.from_json, BenchmarkManifest.from_json, SolverView.from_json],
)
def test_json_readers_normalize_unpaired_unicode_to_value_error(
    reader: Callable[[str], object],
) -> None:
    with pytest.raises(ValueError, match="not valid UTF-8 text"):
        reader("\ud800")

    with pytest.raises(ValueError, match="not valid UTF-8 text"):
        CounterRNG("\ud800")


def test_expression_parser_enforces_depth_and_node_budgets_with_value_errors() -> None:
    deep: dict[str, object] = {"op": "const", "value": 1}
    for _ in range(MAX_EXPRESSION_DEPTH):
        deep = {"op": "neg", "args": [deep]}
    with pytest.raises(ValueError, match="depth budget"):
        Expr.from_dict(deep)

    constructed = Expr.const(1)
    for _ in range(MAX_EXPRESSION_DEPTH):
        constructed = Expr(ExprOp.NEG, args=(constructed,))
    with pytest.raises(ValueError, match="depth budget"):
        constructed.to_dict()

    wide: dict[str, object] = {"op": "const", "value": 1}
    for _ in range(8):
        wide = {"op": "add", "args": [wide, wide]}
    with pytest.raises(ValueError, match="node budget"):
        Expr.from_dict(wide)


def test_steps_strings_and_wrong_types_fail_with_controlled_value_errors() -> None:
    solution = generate_item("budget-shapes", 2).clean_solution.to_dict()
    solution["steps"] = [solution["steps"][0]] * (MAX_SOLUTION_STEPS + 1)
    with pytest.raises(ValueError, match="between 1 and 64"):
        Solution.from_dict(solution)

    raw = generate_item("budget-string", 1).to_dict()
    raw["problem"]["prompt"] = "p" * (MAX_STRING_BYTES + 1)
    with pytest.raises(ValueError, match="byte budget"):
        BenchmarkItem.from_dict(raw)

    raw = generate_item("budget-type", 1).to_dict()
    raw["problem"]["prompt"] = None
    with pytest.raises(ValueError, match="must be a string"):
        BenchmarkItem.from_dict(raw)


def test_mathir_parser_recertifies_serialized_mathematics() -> None:
    raw = generate_item("deserialize-adversary", 4).to_dict()
    target = raw["fault"]["step_number"] - 1
    number = target + 1
    raw["faulty_solution"]["steps"][target]["text"] = f"{number}. hidden extra change."
    with pytest.raises(ValueError, match="content digest"):
        BenchmarkItem.from_dict(raw)


def test_expression_interpreter_uses_exact_rationals() -> None:
    expression = Expr(
        ExprOp.DIV,
        args=(Expr.const(1), Expr.const(3)),
    )
    assert str(evaluate_expression(expression, {})) == "1/3"


def test_expression_interpreter_bounds_numerator_denominator_and_power_preflight() -> None:
    hostile_power = Expr.const(2)
    for _ in range(4):
        hostile_power = Expr(
            ExprOp.POW,
            args=(hostile_power, Expr.const(12)),
        )
    with pytest.raises(ValueError, match="exact-fraction bit budget"):
        evaluate_expression(hostile_power, {})

    large_but_allowed = Expr(
        ExprOp.POW,
        args=(Expr.const(10**12), Expr.const(12)),
    )
    denominator_overflow = Expr(
        ExprOp.DIV,
        args=(
            Expr(ExprOp.DIV, args=(Expr.const(1), large_but_allowed)),
            Expr.const(10**12),
        ),
    )
    with pytest.raises(ValueError, match=f"{MAX_FRACTION_BITS}-bit"):
        evaluate_expression(denominator_overflow, {})


def test_recomputed_digest_hostile_item_hits_fraction_budget_quickly() -> None:
    item = generate_item("hostile-fraction-item", 0)
    target = next(
        step.number for step in item.clean_solution.steps if step.number != item.fault.step_number
    )
    hostile = Expr.const(2)
    for _ in range(4):
        hostile = Expr(ExprOp.POW, args=(hostile, Expr.const(12)))
    relation = Relation(hostile, Expr.const(0))
    replacement = {
        "assertion": relation.to_dict(),
        "number": target,
        "text": f"{target}. {render_relation(relation)}.",
    }
    raw = item.to_dict()
    for variant in ("clean_solution", "faulty_solution"):
        raw[variant]["steps"][target - 1] = replacement
    raw["repair"]["expected_faulty_digest"] = _canonical_digest(raw["faulty_solution"])
    raw["repair"]["expected_repaired_digest"] = _canonical_digest(raw["clean_solution"])
    _rebind_content_digest(raw)

    started = time.perf_counter()
    with pytest.raises(ValueError, match="exact-fraction bit budget"):
        BenchmarkItem.from_dict(raw)
    assert time.perf_counter() - started < 1.0


def test_every_family_has_clean_faulty_and_repaired_validation() -> None:
    observed_families: set[str] = set()
    observed_faults: set[FaultKind] = set()
    for item in generate_bank("family-coverage", 500):
        observed_families.add(item.problem.family)
        observed_faults.add(item.fault.kind)
        assert differing_steps(item.clean_solution, item.faulty_solution) == (
            item.fault.step_number,
        )
        assert all(report.passed for report in validate_solution(item, item.clean_solution))
        faulty_reports = validate_solution(item, item.faulty_solution)
        assert [report.validator for report in faulty_reports] == [
            "assertion-evaluator-v1",
            "independent-family-verifier-v1",
        ]
        assert all(not report.passed for report in faulty_reports)
        assert all(
            report.failing_steps == (item.fault.step_number,) for report in faulty_reports
        )
        repaired = replay_repair(item)
        assert repaired == item.clean_solution
        assert all(report.passed for report in validate_solution(item, repaired))

    assert observed_families == set(FAMILY_ORDER)
    assert observed_faults == set(FAULT_ORDER)


def test_one_cycle_covers_every_family_fault_pair_and_varies_mutation_sites() -> None:
    items = list(generate_bank("family-fault-cycle", len(FAMILY_ORDER) * len(FAULT_ORDER)))
    assert {
        (item.problem.family, item.fault.kind)
        for item in items
    } == {
        (family, kind)
        for family in FAMILY_ORDER
        for kind in FAULT_ORDER
    }
    by_kind: dict[FaultKind, list[BenchmarkItem]] = defaultdict(list)
    for item in items:
        by_kind[item.fault.kind].append(item)
    assert len({item.fault.step_number for item in items}) >= 6
    assert len({item.fault.ast_path for item in items}) >= 12
    assert all(len({item.fault.step_number for item in group}) >= 4 for group in by_kind.values())
    assert all(len({item.fault.ast_path for item in group}) >= 5 for group in by_kind.values())
    assert all(item.fault.mutation for item in items)


def test_clean_steps_are_numbered_in_both_structure_and_rendering() -> None:
    for item in generate_bank("numbering", 50):
        for expected, step in enumerate(item.clean_solution.steps, 1):
            assert step.number == expected
            assert step.text.startswith(f"{expected}. ")


def test_repair_replay_refuses_a_changed_input() -> None:
    item = generate_item("repair-precondition", 0)
    changed = replace(item.faulty_solution, conclusion=item.faulty_solution.conclusion + " changed")
    with pytest.raises(ValueError, match="input digest"):
        replay_repair(item, changed)


def test_certifier_rejects_a_second_changed_line() -> None:
    item = generate_item("second-fault", 9)
    second_step = 1 if item.fault.step_number != 1 else 2
    raw = item.to_dict()
    raw["faulty_solution"]["steps"][second_step - 1]["text"] = f"{second_step}. altered."
    _rebind_content_digest(raw)
    with pytest.raises(ValueError, match="exactly its recorded line"):
        BenchmarkItem.from_dict(raw)


def test_manifest_has_a_pinned_merkle_commitment_and_verifies() -> None:
    manifest = generate_manifest("known-answer-seed", 25)
    assert manifest.item_count == 25
    assert manifest.family_counts == (
        ("arithmetic_progression", 2),
        ("base_conversion", 2),
        ("crt_two_moduli", 2),
        ("determinant_3x3", 1),
        ("gcd_euclidean", 1),
        ("geometric_progression", 1),
        ("horner_polynomial", 1),
        ("hypergeometric_probability", 1),
        ("linear_equation", 1),
        ("modular_inverse", 1),
        ("monomial_integral", 1),
        ("polynomial_derivative", 1),
        ("pythagorean_hypotenuse", 1),
        ("quadratic_integer_roots", 1),
        ("recurrence", 2),
        ("subset_count", 2),
        ("triangle_area", 2),
        ("two_by_two_system", 2),
    )
    assert manifest.merkle_root.startswith("sha256:")
    assert manifest.semantic_root == (
        "sha256:d709a38c8d0516fe8972b5f2a926b14e82b1b4b3f57ddea1dcfe60db478563ba"
    )
    assert verify_manifest(manifest)


def test_manifest_records_nonzero_start_for_self_contained_replay() -> None:
    manifest = generate_manifest("windowed-bank", 12, start=100)
    assert manifest.start_index == 100
    assert verify_manifest(manifest)
    assert manifest.first_item_id == generate_item("windowed-bank", 100).item_id
    assert manifest.last_item_id == generate_item("windowed-bank", 111).item_id


def test_manifest_input_rejects_self_asserted_provenance_changes() -> None:
    raw = generate_item("tamper-evidence", 1).to_dict()
    raw["provenance"]["license_expression"] = "LicenseRef-Owner-Approved-Test-Fixture"
    _rebind_content_digest(raw)
    with pytest.raises(ValueError, match="noncanonical private benchmark license"):
        BenchmarkItem.from_dict(raw)


def test_manifest_rejects_reordering_gaps_and_mixed_seeds() -> None:
    items = list(generate_bank("ordered", 4))
    with pytest.raises(ValueError, match="consecutive"):
        build_manifest([items[0], items[2]])
    with pytest.raises(ValueError, match="one seed"):
        build_manifest([items[0], generate_item("other-seed", 1)])
    with pytest.raises(ValueError, match="explicit seed"):
        build_manifest((), start=0)


def test_manifest_rejects_repeated_canonical_givens_even_with_distinct_ids(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first, second = list(generate_bank("semantic-repeat-guard", 2))
    object.__setattr__(second, "problem", first.problem)
    monkeypatch.setattr(benchmark_module, "certify_item", lambda item: None)
    with pytest.raises(ValueError, match="repeated canonical givens"):
        build_manifest([first, second])


def test_manifest_json_is_canonical_and_self_describing() -> None:
    manifest = generate_manifest("manifest-json", 5)
    restored = BenchmarkManifest.from_json(manifest.canonical_json())
    assert restored == manifest
    parsed = json.loads(restored.canonical_json())
    assert parsed["schema"] == "org.math-research-brain/benchmark-manifest"
    assert parsed["schema_version"] == MANIFEST_SCHEMA_VERSION == 4
    assert parsed["merkle_algorithm"] == "rfc6962-sha256"
    assert parsed["schedule_algorithm"] == "balanced-affine-without-replacement-v1"
    assert parsed["generator_version"] == GENERATOR_VERSION == "synthetic-v4-independent"
    assert sum(parsed["family_counts"].values()) == parsed["item_count"]
    assert sum(parsed["fault_counts"].values()) == parsed["item_count"]
    assert sum(parsed["fault_location_counts"].values()) == parsed["item_count"]
    assert sum(parsed["family_fault_counts"].values()) == parsed["item_count"]


def test_manifest_requires_exact_keys_and_trusted_regeneration() -> None:
    raw = generate_manifest("trusted-manifest", 5).to_dict()
    raw["solver_supplied_label"] = "must never control evaluator dispatch"
    with pytest.raises(ValueError, match="extra"):
        BenchmarkManifest.from_dict(raw)

    tampered = generate_manifest("trusted-manifest", 5).to_dict()
    tampered["merkle_root"] = "sha256:" + "0" * 64
    parsed = BenchmarkManifest.from_dict(tampered)
    assert not verify_manifest(parsed)


def test_manifest_rejects_unsupported_and_inconsistent_count_commitments() -> None:
    unsupported = generate_manifest("manifest-count-binding", 72).to_dict()
    unsupported["fault_counts"]["invented_fault"] = unsupported["fault_counts"].pop(
        "off_by_one"
    )
    with pytest.raises(ValueError, match="unsupported fault kind"):
        BenchmarkManifest.from_dict(unsupported)

    inconsistent = generate_manifest("manifest-count-binding", 144).to_dict()
    keys = sorted(inconsistent["family_fault_counts"])
    donor, recipient = keys[0], keys[-1]
    inconsistent["family_fault_counts"][donor] -= 1
    inconsistent["family_fault_counts"][recipient] += 1
    with pytest.raises(ValueError, match="subtotals disagree"):
        BenchmarkManifest.from_dict(inconsistent)


@pytest.mark.parametrize("legacy_version", [1, 2, 3])
def test_manifest_parser_rejects_unbounded_counts_and_legacy_version(
    legacy_version: int,
) -> None:
    raw = generate_manifest("manifest-budget", 2).to_dict()
    raw["item_count"] = MAX_MANIFEST_ITEMS + 1
    raw["family_counts"] = {"linear_equation": MAX_MANIFEST_ITEMS + 1}
    with pytest.raises(ValueError, match="item count"):
        BenchmarkManifest.from_dict(raw)

    legacy = generate_manifest("legacy-manifest", 2).to_dict()
    legacy["schema_version"] = legacy_version
    with pytest.raises(ValueError, match="legacy manifests"):
        BenchmarkManifest.from_dict(legacy)

    with pytest.raises(ValueError, match=r"\[0, 10000\]"):
        generate_manifest("too-many", MAX_MANIFEST_ITEMS + 1)


def test_generator_yields_exactly_ten_thousand_unique_balanced_certified_items() -> None:
    manifest = generate_manifest("public-10000-regression", 10_000)
    families = dict(manifest.family_counts)
    assert manifest.item_count == 10_000
    assert set(families) == set(FAMILY_ORDER)
    assert max(families.values()) - min(families.values()) == 1
    assert sorted(families.values()) == [555] * 8 + [556] * 10
    assert dict(manifest.fault_counts) == {
        kind.value: 2_500 for kind in FAULT_ORDER
    }
    assert len(manifest.fault_location_counts) >= 6
    assert sum(count for _, count in manifest.fault_location_counts) == 10_000
    assert len(manifest.family_fault_counts) == len(FAMILY_ORDER) * len(FAULT_ORDER)
    assert all(count > 0 for _, count in manifest.family_fault_counts)
    assert manifest.merkle_root.startswith("sha256:")
    assert manifest.semantic_root.startswith("sha256:")
    assert verify_manifest(manifest)
