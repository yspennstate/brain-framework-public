---
problem_id: poincare_birkhoff
short_name: Poincaré–Birkhoff theorem (Poincaré's last geometric theorem)
status: classical (proved 1913, mathlib4 formalization is open)
posed_by: Henri Poincaré (conjectured 1912)
proved_by: George D. Birkhoff (1913, completed proof; partial proof by Poincaré 1912)
domain: dynamical systems / symplectic topology
difficulty: classical theorem; mathlib4 formalization is novel and would be a real contribution
---

# Poincaré–Birkhoff Theorem (twist map theorem)

## Informal statement (classical Birkhoff form)

Let \(A = S^1 \times [0,1]\) be the closed annulus. Let \(f : A \to A\) be a homeomorphism such that:

1. **Area preservation:** \(f\) preserves the standard Lebesgue area form on \(A\).
2. **Boundary preservation:** \(f(S^1 \times \{0\}) = S^1 \times \{0\}\) and \(f(S^1 \times \{1\}) = S^1 \times \{1\}\).
3. **Twist condition:** there is a lift \(\tilde f : \mathbb{R} \times [0,1] \to \mathbb{R} \times [0,1]\) of \(f\)
   such that the rotation numbers of \(\tilde f\) on the two boundaries have **opposite signs**:
   \[
   \rho(\tilde f|_{y=0}) > 0 > \rho(\tilde f|_{y=1}) \qquad \text{or} \qquad \rho(\tilde f|_{y=0}) < 0 < \rho(\tilde f|_{y=1}).
   \]

**Theorem (Poincaré–Birkhoff).** Under (1), (2), (3), \(f\) has at least **two** distinct fixed points in the
interior of \(A\).

## Why this is the right benchmark for ProofSwarm

1. **Status.** The theorem is **proved** (Birkhoff 1913; rigorous modern proofs by Brown–Neumann 1977 and many later refinements). So success at full proof is in principle possible — unlike Furstenberg ×2 ×3 where the goal is partial progress.
2. **Not in mathlib4.** As of 2026-05, mathlib4 has Brouwer fixed point (`Mathlib.Analysis.NormedSpace.BrowerFixedPoint`), Lefschetz, Banach contraction, but **not** Poincaré–Birkhoff. Formalization would be a real mathlib contribution.
3. **Numerical handles.** For any explicit twist map \(f_\alpha(\theta, y) = (\theta + \alpha(y), y)\) with
   \(\alpha(0) > 0 > \alpha(1)\), the theorem predicts ≥ 2 fixed points. We can numerically search for them
   on parameter grids and produce certificates.
4. **Proof has structure.** Modern proofs decompose into: (a) reduce to lifts, (b) area-preservation via
   generating functions, (c) variational argument or index theory. Each is a sub-ticket.
5. **Wrong-statement trap.** The theorem is delicate — sloppy statements ("at least one fixed point" suffices,
   forget the area-preserving hypothesis, drop the twist condition) lead to immediate counterexamples.
   Excellent test of the Referee.

## Sub-ticket decomposition

| ID | Statement |
|----|-----------|
| **L1** | Formalize the Poincaré–Birkhoff theorem statement in Lean 4: closed annulus, area-preserving boundary-fixing homeomorphism, twist condition, conclusion ≥ 2 fixed points. |
| **L2** | Formalize a **simpler version**: area-preserving homeomorphism of the annulus with twist condition has at least **one** fixed point (this is strictly weaker; Brouwer-style argument). |
| **L3** | Formalize the **standard twist map family** \(f_{\alpha,\epsilon}(\theta, y) = (\theta + \alpha(y) + \epsilon V_\theta(\theta, y),\; y - \epsilon V_y(\theta, y))\) with generating function \(V\); state that for small \(\epsilon\) it preserves area. |
| **L4** | **Numerical certificate**: for an explicit twist map \(f(\theta, y) = (\theta + (1-2y) + \epsilon \sin(\theta), y)\) with small \(\epsilon\), find ≥ 2 fixed points via Newton's method on a high-resolution grid and certify the count. |
| **L5** | Lemma: the **closed-set argument** — fixed points are zeros of \(\theta \mapsto F(\theta, y) - \theta \pmod 1\); use intermediate-value-theorem-like topology on the annulus. |
| **L6** | Counterexample search: **drop the area-preservation hypothesis** and exhibit a twist map with 0 or 1 fixed points (this verifies area preservation is essential — there are explicit examples). |
| **L7** | The **rotation-number lemma**: for orientation-preserving circle homeomorphisms, the rotation number is well-defined and continuous. (Proved in mathlib? Check.) |

## Concrete twist map family for L4 numerical work

**Family.** \(f(\theta, y) = (\theta + (1 - 2y) + \epsilon \sin(2\pi \theta),\; y)\) on \(S^1 \times [0,1]\),
with \(\epsilon \in [-0.3, 0.3]\) small.

- **Boundary rotation numbers.** \(f(\theta, 0) = (\theta + 1 + \epsilon \sin(2\pi\theta), 0)\) — rotation number \(+1\).
  \(f(\theta, 1) = (\theta - 1 + \epsilon \sin(2\pi\theta), 1)\) — rotation number \(-1\).
  So twist condition holds for any \(|\epsilon| < 1/(2\pi)\).
- **Note.** This map has constant \(y\)-coordinate, so it is **NOT** area-preserving for \(\epsilon \neq 0\)
  in the strong sense — it's a degenerate case. For a genuine area-preserving twist, use generating function
  \(S(\theta, y) = \theta y + \epsilon \cos(2\pi\theta) \cdot g(y)\) with appropriate \(g\). The Counterexample
  Hunter's job is to identify which family members are genuine and which aren't.
- **Fixed points.** Of \(f(\theta,y) = (\theta,y)\) in the family above: solve \(\theta + (1-2y) + \epsilon\sin(2\pi\theta) = \theta \pmod 1\),
  i.e. \(2y - 1 = \epsilon \sin(2\pi\theta) \pmod 1\). For small \(\epsilon\), there are exactly TWO families
  of fixed points parameterized by which branch of \(2y\) you take.

## References (Lemma Miner seed corpus)

- Birkhoff, G. D. (1913). Proof of Poincaré's geometric theorem. Trans. Amer. Math. Soc. 14, 14–22.
- Birkhoff, G. D. (1925). An extension of Poincaré's last geometric theorem. Acta Math. 47, 297–311.
- Brown, M. & Neumann, W. D. (1977). Proof of the Poincaré–Birkhoff fixed point theorem. Michigan Math. J.
- Carter, P. H. (1982). An improvement of the Poincaré–Birkhoff theorem. Trans. AMS 269.
- Mathlib4: `Mathlib.Analysis.NormedSpace.BrouwerFixedPoint`, `Mathlib.Topology.Maps.OpenQuotient`.

## Evaluation criteria (POC scope)

- **L1 verdict** by Referee: at minimum a non-blocker formalization; ideally `lean_verified` if Prover can fill `sorry`. Realistic: `refuted_by_referee` first pass, repair to `open` (statement-only) on second pass.
- **L4 numerical certificate**: count fixed points of explicit twist map, sup-norm Newton residual, sha256 of parameter grid.
- **L6 counterexample**: produce an explicit non-area-preserving twist with < 2 fixed points (this is a known textbook example — confirms theorem statement is tight).
- **Cost cap**: stay under $10 of credits for the full run.
