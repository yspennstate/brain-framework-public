"""Numerical tools for Smale's Mean Value Conjecture.

Given polynomial p(z) = sum a_k z^k with p(0)=0:
  - find critical points (roots of p')
  - compute |p(zeta_i) / (zeta_i p'(0))| for each
  - return min over i (this is what Smale conjectures ≤ (n-1)/n)
"""
from __future__ import annotations
import json
import hashlib
from dataclasses import dataclass
from typing import Iterable

import numpy as np


@dataclass
class SmaleEvaluation:
    coeffs: list[complex]   # coefficients [a_1, a_2, ..., a_n] with p(z) = a_1 z + a_2 z^2 + ...
    n: int
    critical_points: list[complex]
    ratios: list[float]
    min_ratio: float
    bound_n_minus_1_over_n: float
    smale_holds: bool       # min_ratio <= (n-1)/n


def evaluate_polynomial(coeffs: Iterable[complex]) -> SmaleEvaluation:
    """coeffs: [a_1, a_2, ..., a_n] with p(0)=0 enforced (a_0 omitted).
    p(z) = a_1 z + a_2 z^2 + ... + a_n z^n.
    """
    a = list(coeffs)
    n = len(a)
    # poly1d uses high-to-low order; build [a_n, a_{n-1}, ..., a_1, 0]
    p_coeffs = list(reversed(a)) + [0]   # constant term 0 since p(0)=0
    p = np.poly1d(p_coeffs)
    p_prime = p.deriv()
    # critical points = roots of p'
    crit = np.roots(p_prime.coeffs) if len(p_prime.coeffs) > 1 else np.array([], dtype=complex)
    p_prime_at_0 = p_prime(0)
    if abs(p_prime_at_0) < 1e-15:
        # degenerate: a_1 = 0
        return SmaleEvaluation(
            coeffs=a, n=n, critical_points=list(crit),
            ratios=[float('inf')] * len(crit),
            min_ratio=float('inf'),
            bound_n_minus_1_over_n=(n - 1) / n,
            smale_holds=False,
        )
    ratios: list[float] = []
    for zeta in crit:
        if abs(zeta) < 1e-15:
            ratios.append(float('inf'))   # ζ=0 → division by zero; skip in min
            continue
        ratio = abs(p(zeta) / (zeta * p_prime_at_0))
        ratios.append(float(ratio))
    valid = [r for r in ratios if r != float('inf')]
    min_r = min(valid) if valid else float('inf')
    bound = (n - 1) / n
    return SmaleEvaluation(
        coeffs=a, n=n, critical_points=list(crit),
        ratios=ratios, min_ratio=min_r,
        bound_n_minus_1_over_n=bound,
        smale_holds=min_r <= bound + 1e-9,
    )


def search_lower_bound(n: int, n_random: int = 10_000,
                       coeff_range: float = 5.0,
                       seed: int = 0,
                       sampling: str = "uniform") -> dict:
    """Random search for polynomials of degree n that maximize min_ratio (lower bound on K(n)).

    sampling: "uniform" (basic) | "perturb_extremal" (perturb conjectured extremal)
              | "rotational" (sample on circle of varying radii) | "mixed" (rotate through all)
    """
    rng = np.random.default_rng(seed)
    best_min_ratio = 0.0
    best_coeffs: list[complex] | None = None
    n_violations = 0
    history = []
    extremal = [1.0 + 0j] + [0.0 + 0j] * (n - 2) + [(n - 1) / n + 0j]
    for k in range(n_random):
        mode = sampling
        if sampling == "mixed":
            mode = ["uniform", "perturb_extremal", "rotational"][k % 3]
        if mode == "uniform":
            re = rng.uniform(-coeff_range, coeff_range, n)
            im = rng.uniform(-coeff_range, coeff_range, n)
            coeffs = (re + 1j * im).tolist()
        elif mode == "perturb_extremal":
            scale = rng.uniform(0.001, 0.5)
            re_pert = rng.normal(0, scale, n)
            im_pert = rng.normal(0, scale, n)
            coeffs = [c + r + 1j * i for c, r, i in zip(extremal, re_pert, im_pert)]
        elif mode == "rotational":
            radius = rng.uniform(0.1, 3.0)
            angles = rng.uniform(0, 2 * np.pi, n)
            mags = rng.uniform(0.01, radius, n)
            coeffs = [m * (np.cos(a) + 1j * np.sin(a)) for m, a in zip(mags, angles)]
        else:
            re = rng.uniform(-coeff_range, coeff_range, n)
            im = rng.uniform(-coeff_range, coeff_range, n)
            coeffs = (re + 1j * im).tolist()
        coeffs[0] = 1.0 + 0j  # normalize p'(0) = 1
        if abs(coeffs[-1]) < 0.05:
            coeffs[-1] = 1.0 + 0j  # ensure degree n
        ev = evaluate_polynomial(coeffs)
        if ev.min_ratio > best_min_ratio and ev.min_ratio != float('inf'):
            best_min_ratio = ev.min_ratio
            best_coeffs = coeffs
            history.append({"iter": k, "min_ratio": ev.min_ratio,
                            "smale_holds": ev.smale_holds, "sampling_mode": mode})
        if not ev.smale_holds and ev.min_ratio != float('inf'):
            n_violations += 1
    bound_conjectured = (n - 1) / n
    cert_data = {
        "n": n,
        "n_random": n_random,
        "sampling": sampling,
        "best_min_ratio": best_min_ratio,
        "bound_conjectured": bound_conjectured,
        "best_min_ratio_vs_bound": best_min_ratio / bound_conjectured if bound_conjectured else None,
        "n_violations": n_violations,
        "best_coeffs_real": [c.real for c in best_coeffs] if best_coeffs else None,
        "best_coeffs_imag": [c.imag for c in best_coeffs] if best_coeffs else None,
        "history_len": len(history),
    }
    cert_sha = hashlib.sha256(json.dumps(cert_data, sort_keys=True).encode()).hexdigest()
    return {"certificate": cert_data, "sha256": cert_sha,
            "history_summary": history[-5:]}


def gradient_search(n: int, n_seeds: int = 20, max_iter: int = 200,
                    seed: int = 0) -> dict:
    """Local optimization: from random seeds, gradient-ascent on min_ratio.

    Uses scipy.optimize. Smooth approximation of min via log-sum-exp with negative beta.
    """
    try:
        from scipy.optimize import minimize
    except ImportError:
        return {"error": "scipy not installed"}
    rng = np.random.default_rng(seed)

    def neg_smoothed_min(x_flat: np.ndarray, beta: float = 50.0) -> float:
        # x_flat = [a2_re, a2_im, ..., an_re, an_im]; a1 fixed at 1+0j
        coeffs = [1.0 + 0j]
        for i in range(n - 1):
            coeffs.append(x_flat[2*i] + 1j * x_flat[2*i+1])
        ev = evaluate_polynomial(coeffs)
        valid = [r for r in ev.ratios if r != float('inf') and not np.isnan(r)]
        if not valid:
            return 1e6
        # smooth min via -log(sum exp(-beta * r)) / beta — encourages min to be high
        # we want to MAXIMIZE min, so MINIMIZE -min ≈ +log(...)/beta
        arr = np.array(valid)
        smin = -np.log(np.sum(np.exp(-beta * arr))) / beta
        return -smin  # negate because minimize wants min

    best_min_ratio = 0.0
    best_coeffs: list[complex] | None = None
    for seed_i in range(n_seeds):
        # Initial: perturbed extremal
        x0 = np.zeros(2 * (n - 1))
        x0[-2] = (n - 1) / n  # a_n_re
        x0 += rng.normal(0, 0.3, len(x0))
        try:
            res = minimize(neg_smoothed_min, x0, method='L-BFGS-B',
                          options={'maxiter': max_iter, 'ftol': 1e-9})
        except Exception:
            continue
        coeffs = [1.0 + 0j]
        for i in range(n - 1):
            coeffs.append(res.x[2*i] + 1j * res.x[2*i+1])
        ev = evaluate_polynomial(coeffs)
        if ev.min_ratio > best_min_ratio and ev.min_ratio != float('inf'):
            best_min_ratio = ev.min_ratio
            best_coeffs = coeffs
    return {
        "n": n,
        "method": "gradient_lbfgsb",
        "n_seeds": n_seeds,
        "max_iter": max_iter,
        "best_min_ratio": best_min_ratio,
        "bound_conjectured": (n - 1) / n,
        "best_coeffs_real": [c.real for c in best_coeffs] if best_coeffs else None,
        "best_coeffs_imag": [c.imag for c in best_coeffs] if best_coeffs else None,
    }


def evaluate_known_extremal_family(n: int) -> dict:
    """Evaluate the conjectured extremal: p(z) = z + ((n-1)/n) z^n.
    Should give min_ratio close to (n-1)/n (saturating the conjectured bound).
    """
    coeffs = [1.0 + 0j] + [0.0 + 0j] * (n - 2) + [(n - 1) / n + 0j]
    ev = evaluate_polynomial(coeffs)
    return {
        "n": n,
        "polynomial": "p(z) = z + ((n-1)/n) z^n",
        "coeffs": coeffs,
        "min_ratio": ev.min_ratio,
        "bound_conjectured": ev.bound_n_minus_1_over_n,
        "smale_holds": ev.smale_holds,
        "critical_points": [(c.real, c.imag) for c in ev.critical_points],
    }


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore
    print("=== Known extremal family p(z) = z + ((n-1)/n) z^n ===")
    for n in [2, 3, 4, 5, 6, 7, 8]:
        r = evaluate_known_extremal_family(n)
        print(f"  n={n}: min_ratio={r['min_ratio']:.6f}  bound={r['bound_conjectured']:.6f}  "
              f"holds={r['smale_holds']}")
    print()
    print("=== Random search lower bound for K(5) (5000 random polynomials) ===")
    r = search_lower_bound(n=5, n_random=5000)
    cert = r["certificate"]
    print(f"  best_min_ratio (lower bound on K(5)): {cert['best_min_ratio']:.6f}")
    print(f"  conjectured bound (n-1)/n = {cert['bound_conjectured']:.6f}")
    print(f"  ratio: {cert['best_min_ratio_vs_bound']:.4f}")
    print(f"  violations of Smale conjecture: {cert['n_violations']}")
    print(f"  cert_sha256: {r['sha256'][:16]}...")
