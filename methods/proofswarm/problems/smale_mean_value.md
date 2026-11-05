---
problem_id: smale_mean_value
short_name: Smale's Mean Value Conjecture
status: open (n ≥ 5)
posed_by: Stephen Smale
posed_year: 1981
domain: polynomial dynamics / complex analysis / Newton's method
difficulty: open ≥ 45 years; partial results published
---

# Smale's Mean Value Conjecture (1981)

## Informal statement

Let \(p(z)\) be a complex polynomial of degree \(n \ge 2\) with \(p(0) = 0\) and \(p'(0) \neq 0\).
Let \(\zeta_1, \ldots, \zeta_{n-1}\) be the critical points of \(p\) (zeros of \(p'\)).

**Conjecture (Smale 1981).** There exists a critical point \(\zeta_i\) such that
\[
\left| \frac{p(\zeta_i)}{\zeta_i \cdot p'(0)} \right| \le \frac{n-1}{n}.
\]

Equivalently, define \(K(n) = \sup_{p}\,\min_i \left|\frac{p(\zeta_i)}{\zeta_i p'(0)}\right|\). The conjecture
states \(K(n) = (n-1)/n\).

## Status (as of 2026)

| n | Status | Reference |
|---|--------|-----------|
| 2 | \(K(2) = 1/2\) (trivial) | direct |
| 3 | \(K(3) = 2/3\) | Tischler, 1989 |
| 4 | \(K(4) = 3/4\) | Beardon, Minda, Ng, 2002 |
| **5** | **OPEN.** Best known: \(K(5) \le 4\). Conjecture: \(K(5) = 4/5\). | — |
| 6+ | Open. General bound \(K(n) \le 4\) (Beardon-Minda-Ng) | — |

Even **improving the bound for** \(n=5\) below 4 toward 4/5 = 0.8 would be a publishable result.

## Why this fits ProofSwarm autonomous mode

1. **Concrete polynomial inequality.** No exotic types or definitions needed. Sympy can verify
   any specific polynomial example. Lean has `Mathlib.Analysis.Calculus.Polynomial`.
2. **Sub-conjectures are isolated.** Each \(n\) is a separate problem. Improving the bound for
   any one \(n\) is real progress.
3. **Counterexample-searchable.** For specific polynomial families, we can numerically compute
   \(\min_i |p(\zeta_i)/(\zeta_i p'(0))|\) and search for extremal cases.
4. **Lean handles n=2, n=3, n=4** with reasonable effort. The known proofs are computer-aided
   (n=3 uses a discriminant argument; n=4 uses a clever choice of critical point).
5. **Long-horizon target.** Even after days of agent-time, success is unlikely on the OPEN cases —
   but the partial work (numerical bounds, formalized known cases, attempted lemma decompositions,
   failed approaches) is itself a research artifact.

## Sub-ticket decomposition

| ID | Statement |
|----|-----------|
| **S0_def** | Formalize "Smale Mean Value Constant" K(n) and the conjecture in Lean 4. |
| **S1_n2** | Prove K(2) = 1/2. (Trivial.) |
| **S2_n3** | Prove K(3) = 2/3 (Tischler). |
| **S3_n4** | Prove K(4) = 3/4 (Beardon–Minda–Ng). |
| **S4_n5_lower** | Find polynomial of degree 5 with min ratio ≥ c for largest c (lower bound on K(5)). |
| **S5_n5_upper** | Improve general upper bound K(5) ≤ 4 toward K(5) ≤ 4/5. Even K(5) ≤ 3 would be progress. |
| **S6_extremal** | Characterize extremal polynomials: which polynomials achieve K(n)? Conjecture: \(p(z) = z(1+z/n)^{n-1}\) family. |
| **S7_dual** | Dual conjecture: every critical point gives a "low value" / "weak" version. |
| **S8_complex** | Restrict to real-coefficient polynomials — does the bound improve? |
| **S9_numerical** | Massive numerical search over polynomial families to find new lower bounds for K(n), n ∈ {5,6,7,8}. |
| **S10_creative_var** | Variational / optimization approach: cast K(n) as a minimax over polynomial coefficients. |
| **S11_creative_funct** | Functional-analytic approach: relate K(n) to operator norms on polynomial spaces. |
| **S12_creative_alg** | Algebraic geometry approach: discriminant variety methods. |

## Numerical handles

For \(p(z) = z + a_2 z^2 + \cdots + a_n z^n\) with \(a_n \neq 0\):
- Compute \(\zeta_i\) as roots of \(p'(z)\) via numerical root finding
- For each \(\zeta_i\), compute ratio \(|p(\zeta_i) / (\zeta_i p'(0))|\)
- Minimum over \(i\) gives the relevant value
- Goal: maximize this min over choice of polynomial → numerical lower bound on K(n)

The conjectured extremal: \(p(z) = z + \frac{n-1}{n} z^n\) (or variants).

## "Verifiable solution" criteria (when to stop)

- **Lean-verified theorem** for n=2, 3, or 4 (low bar — just confirms architecture works)
- **Improved lower bound** for K(5): numerically certify a polynomial with min-ratio higher than current best, with sympy verification of the polynomial's critical points
- **Improved upper bound** for K(5): Lean-checked or sympy-checked algebraic argument that K(5) ≤ c for some c < 4
- **Counterexample to conjecture**: a polynomial showing K(n) > (n−1)/n (would refute Smale conjecture — extraordinary)

## References

- Smale, S. (1981). The fundamental theorem of algebra and complexity theory. Bull. AMS 4, 1–36.
- Tischler, D. (1989). Critical points and values of complex polynomials. J. Complexity 5, 438–456.
- Beardon, A. F., Minda, D., Ng, T. W. (2002). Smale's mean value conjecture and the hyperbolic metric. Math. Ann. 322.
- Conte, A., Fujikawa, E., Lakic, N. (2007). Smale's mean value conjecture and the coefficients of univalent functions.
- Crane, E. (2007). Bounds for the modulus of polynomials. PhD thesis.
