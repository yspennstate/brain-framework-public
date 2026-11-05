# Deterministic error-detection benchmark

The benchmark is a fixed-capacity bank of 10,000 synthetic mathematics problems. It reads no
corpus and no other local project. A seed and integer coordinate replay an evaluator record, while
the solver receives only the problem and numbered derivation.

MathIR v4 records carry canonical public givens, an answer, an exact witness, a clean certificate,
a copy with one false relation, the declared AST mutation, and a hash-preconditioned one-line
repair. The generator is synthetic-v4-independent.

## Mathematical coverage

The bank has eighteen families:

1. arithmetic progression sums;
2. base conversion;
3. two-modulus Chinese remainder problems;
4. 3 by 3 determinants;
5. Euclidean gcd calculations;
6. geometric progression sums;
7. polynomial evaluation by Horner's rule;
8. hypergeometric probabilities;
9. integer linear equations;
10. modular inverses;
11. definite monomial integrals;
12. polynomial derivatives at a point;
13. integer Pythagorean hypotenuses;
14. larger roots of integer quadratics;
15. first-order affine recurrences;
16. subset counts;
17. triangle areas; and
18. 2 by 2 linear systems.

Each certificate is specific to its family. A recurrence trace checks every recurrence step. A base
conversion trace records each quotient and remainder. Gcd traces contain the Euclidean divisions
and a Bezout identity. Combination and hypergeometric traces use bounded binomial recurrences
instead of factorial constants. The remaining families likewise record the equations that
determine the requested answer. There are no common filler lines added after a family calculation.

Serialized problem parameters contain only givens named in the prompt. Answers, roots,
probabilities, witnesses, and intermediate values are derived. The semantic fingerprint hashes the
family and canonical givens, so two records with the same mathematical problem cannot coexist in a
manifest under different coordinates.

## Separate verification

Generation lives in src/math_brain/benchmark.py. Certification calls the separate
src/math_brain/benchmark_verifier.py module, which does not import the generator. It reimplements
all eighteen algorithms and returns neutral expression tuples. The benchmark module converts those
tuples to MathIR and compares the prompt, answer, difficulty, witness, conclusion, and every
numbered relation.

A second validator evaluates each structured equality under the exact witness. Clean traces must
pass both checks. The faulty trace must fail both checks on the one declared line. Repair replay
must recover the clean trace, and deterministic coordinate replay binds the complete record to the
trusted code.

This separation is not treated as mathematical corroboration by itself. Tests also use a third
answer implementation and deliberately poison a generator oracle to confirm that the verifier
rejects generator-only errors.

## Fault placement

Coordinates still schedule four fault kinds in balanced strata:

- off_by_one increments an eligible integer literal;
- sign_error negates an eligible nonzero integer literal;
- dropped_factor removes one child of a multiplication node; and
- wrong_operation swaps addition and subtraction.

The line and AST site are not fixed by fault kind. The generator enumerates all one-node candidates
throughout the family certificate, keeps only mutations that are evaluable and false under the
witness, and selects one with a separate deterministic counter stream. The record stores the full
relation-side AST path. Certification independently enumerates the eligible set and checks the
declared path and mutated relation.

Manifest fault-location counts therefore describe observed step numbers. They are not expected to
match the fault-kind counts or a four-line template.

## Solver-valid scope

A held-out shortcut test learns number-masked solution templates on one seed and scores a disjoint
seed. Off-by-one and sign faults remain at chance after masking, but dropped-factor and
operator-swap cases retain learnable expression-shape cues. A second held-out classifier uses only
the number of literals and the counts of negative, zero, and one literals. That summary is near
chance for off-by-one cases but identifies sign errors far above the validity threshold.

SOLVER_VALID_FAULTS therefore contains only off_by_one. Sign errors, dropped factors, and operator
swaps are certified engineering fuzz cases and must be reported separately. They cannot be folded
into a solver-ability aggregate.

The test suite also removes the prompt, removes all family-certificate lines, and checks
prompt-only, run-local-ID-only, number-masked-template, numeric-summary, and line-shape shortcuts.
Every benefit-bearing shortcut baseline must remain at or below 0.60 on the pinned disjoint seeds. A
character-trigram classifier over the complete solver-visible text is checked at the same
threshold. The broader four-kind results do not restore any fuzz stratum to solver-valid status.

## Solver boundary

BenchmarkItem.solver_assignment creates an HMAC-derived identifier from a secret run nonce. The
same case has the same identifier within a run and a different identifier in another run. The
assignment exposes only that opaque handle and a SolverView. Stable item IDs, coordinates,
digests, answers, witnesses, givens, semantic fingerprints, fault labels, repairs, and provenance
are absent.

The clean and faulty variants of one case share their prompt and run-local identifier. An ID-only
or prompt-only classifier therefore sees one example of each label for every feature value.

## Replay, manifests, and budgets

Family scheduling uses seed-specific affine permutations without replacement. Fault scheduling is
separate from family-domain selection. A manifest commits to the seed, coordinate window, family
and fault counts, observed fault locations, all family/fault counts, boundary identifiers, an RFC
6962 record root, and an RFC 6962 semantic root.

The counter RNG rejects bounds wider than 512 bits. MathIR limits expression depth to 32,
expression size to 256 nodes, solutions to 64 lines, and exact-fraction numerators and denominators
to 512 bits.
Strict JSON readers also enforce aggregate byte limits and reject duplicate keys, malformed types,
unpaired surrogates, and excessive nesting.

A small sample can be generated without writing a file:

    $env:PYTHONPATH = "src"
    python examples/generate_benchmark_sample.py

Full-bank generation remains lazy:

    from math_brain.benchmark import generate_bank, generate_manifest

    items = generate_bank("public-bank-v4", 10_000)
    manifest = generate_manifest("public-bank-v4", 10_000)

To retain the actual certified records, materialize them only under ignored local storage:

    $env:PYTHONPATH = "src"
    python examples/materialize_benchmark.py local/benchmark/v4-10000 --seed public-10000-regression --count 10000

The materializer writes one atomic directory containing canonical `items.jsonl`, `manifest.json`,
and `provenance.json`. It refuses to overwrite an existing destination and fsyncs every file before
publishing the directory.

Do not cite a full-bank v4 result until the gated 10,000-item regression has actually run and its
manifest has been recorded.

## Version and provenance

V4 uses schema version 4, the mathir_v4_ identifier namespace, and the
item-id-v4-independent hash domain. V1, v2, and v3 records and manifests are rejected and must be
regenerated from coordinates.

The v3 breadth bank remains an engineering fuzz corpus. Its common certificate lines and fixed
fault locations allow shortcut classification, so it is not solver-valid evidence.

Provenance records both the normalized benchmark.py hash and a domain-separated bundle hash over
benchmark.py plus benchmark_verifier.py. The declared private license describes repository rights;
it is not permission to redistribute generated records.
