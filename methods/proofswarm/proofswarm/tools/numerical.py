"""Numerical counterexample search.

For Furstenberg ×2 ×3 specifically, the canonical candidate families are:
- Bernoulli convolutions ν_β = law of sum_{n>=1} ε_n β^n with ε_n ∈ {-1,+1} iid Bernoulli(1/2).
- Self-similar IFS measures: μ = sum_i p_i (μ ∘ f_i^{-1}) for affine f_i.
- Atomic measures supported on finite rational sets with denom coprime to 6 (these ARE invariant —
  they're the trivial case the conjecture allows).

Joint invariance test: μ is T-invariant iff μ ∘ T^{-1} = μ. Discretize at resolution N=2^k×3^l so the
maps are exact on the lattice; check L∞ distance between μ and μ ∘ T^{-1} for T ∈ {T_2, T_3}.
"""
from __future__ import annotations
import hashlib
import json
from dataclasses import dataclass

import numpy as np

from proofswarm.schemas import NumericalSearchRequest, NumericalSearchResult


def _discretize_at_resolution(samples: np.ndarray, n_bins: int) -> np.ndarray:
    """Histogram samples into n_bins on [0,1). Returns probability vector."""
    counts, _ = np.histogram(samples, bins=n_bins, range=(0.0, 1.0))
    return counts.astype(np.float64) / max(counts.sum(), 1)


def _pushforward_x_n(p: np.ndarray, n: int) -> np.ndarray:
    """Compute the discrete pushforward of measure p (on [0,1)) under x -> n*x mod 1.

    p has length N where N is divisible by n. The pushforward is constant on intervals
    [j/N, (j+1)/N) and lands at floor(n*j/N + offset) which mod N gives k*n^{-1}-shifted bins.
    Concretely: pushforward[k] = sum_{j: floor(n*j/N) mod N == k} p[j], weighted appropriately.
    For exactness we use the formula: if N = n*M, then T_n([j/N, (j+1)/N)) = [(jn mod N)/N, ((jn+n) mod N)/N).
    """
    N = len(p)
    assert N % n == 0, f"resolution {N} not divisible by {n}"
    out = np.zeros(N)
    for j in range(N):
        # T_n maps the bin [j/N, (j+1)/N) to a length-n/N range, distributed across n contiguous output bins.
        start = (j * n) % N
        # the n-bin contiguous range:
        for k in range(n):
            out[(start + k) % N] += p[j] / n
    return out


def sample_bernoulli_convolution(beta: float, n_samples: int = 200_000, n_terms: int = 64,
                                 rng: np.random.Generator | None = None) -> np.ndarray:
    """Sample from ν_β scaled and shifted to [0,1)."""
    rng = rng or np.random.default_rng(0)
    eps = rng.choice(np.array([-1.0, 1.0]), size=(n_samples, n_terms))
    powers = beta ** np.arange(1, n_terms + 1)
    raw = (eps * powers).sum(axis=1)
    # support of ν_β is [-β/(1-β), β/(1-β)]; shift+scale to [0,1)
    span = beta / (1 - beta)
    x = (raw + span) / (2 * span + 1e-30)
    x = np.clip(x, 0.0, 1.0 - 1e-12)
    return x


def joint_invariance_violation(p: np.ndarray) -> dict[str, float]:
    """Returns L∞ and L1 distance between p and its T_2 / T_3 pushforwards."""
    p2 = _pushforward_x_n(p, 2)
    p3 = _pushforward_x_n(p, 3)
    return {
        "linf_T2": float(np.max(np.abs(p - p2))),
        "linf_T3": float(np.max(np.abs(p - p3))),
        "l1_T2": float(np.sum(np.abs(p - p2))),
        "l1_T3": float(np.sum(np.abs(p - p3))),
    }


def sweep_bernoulli_convolutions(beta_grid: list[float],
                                 resolution: int = 2 * 3 * 2 * 3 * 2 * 3,  # 6^k for divisibility
                                 n_samples: int = 200_000,
                                 tolerance: float = 1e-3) -> NumericalSearchResult:
    """Sweep β values, sample ν_β, check joint T_2/T_3 invariance."""
    counterexamples: list[dict] = []
    violations: list[float] = []
    for beta in beta_grid:
        samples = sample_bernoulli_convolution(beta, n_samples=n_samples)
        p = _discretize_at_resolution(samples, resolution)
        stats = joint_invariance_violation(p)
        max_violation = max(stats["linf_T2"], stats["linf_T3"])
        violations.append(max_violation)
        if max_violation < tolerance:
            counterexamples.append({"family": "bernoulli_convolution",
                                    "beta": beta, "stats": stats})
    cert = {"family": "bernoulli_convolution", "beta_grid": beta_grid,
            "resolution": resolution, "n_samples": n_samples, "tolerance": tolerance,
            "violations": violations}
    cert_sha = hashlib.sha256(json.dumps(cert, sort_keys=True).encode()).hexdigest()
    return NumericalSearchResult(
        candidates_checked=len(beta_grid),
        counterexamples_found=counterexamples,
        sup_norm_violations=violations,
        certificate_sha256=cert_sha,
    )


def run_numerical_search(req: NumericalSearchRequest) -> NumericalSearchResult:
    """Dispatch to the right sweep based on family."""
    if req.family == "bernoulli_convolution":
        beta_grid = req.parameter_grid.get("beta", [0.5, 0.6, 0.7, 0.8])
        return sweep_bernoulli_convolutions(beta_grid, tolerance=req.invariance_tolerance)
    if req.family == "rational_atomic":
        return _check_rational_atomic_invariance(req.parameter_grid.get("denominators", [2, 3, 5, 7, 11]))
    if req.family == "twist_map_fixed_points":
        return _twist_map_fixed_point_search(
            epsilon_grid=req.parameter_grid.get("epsilon", [0.05, 0.1, 0.15, 0.2]),
            grid_n=req.parameter_grid.get("grid_n", [200])[0],
            tolerance=req.invariance_tolerance,
        )
    raise NotImplementedError(f"family={req.family} not yet implemented")


# --- Poincaré-Birkhoff twist map fixed-point search ---
def _generating_function_twist_map(theta: float, y: float, eps: float) -> tuple[float, float]:
    """Twist map with generating function V(θ,y) = y cos(2π θ).

    f(θ,y) = (θ + (1 - 2y) - 2π ε y sin(2π θ),  y - ε cos(2π θ))

    Boundary rotation numbers: f|_{y=0} rotates by +1 (mod 1, so trivial),
    f|_{y=1} rotates by -1. For small ε this is area-preserving (generating-function form).
    The Poincaré-Birkhoff theorem predicts ≥ 2 fixed points; analytically there are
    exactly TWO at θ ∈ {1/4, 3/4} with y = 1/(2 ± 2πε).
    """
    import math
    new_theta = (theta + (1.0 - 2.0 * y) - 2 * math.pi * eps * y * math.sin(2 * math.pi * theta)) % 1.0
    new_y = y - eps * math.cos(2 * math.pi * theta)
    return new_theta, new_y


def _twist_residual(theta: float, y: float, eps: float) -> float:
    """L∞ distance between f(θ,y) and (θ,y) — zero at fixed points."""
    import math
    new_t, new_y = _generating_function_twist_map(theta, y, eps)
    # θ is on circle: distance mod 1
    dt = min(abs(new_t - theta), 1.0 - abs(new_t - theta))
    dy = abs(new_y - y)
    return max(dt, dy)


def _newton_refine(theta0: float, y0: float, eps: float, max_iter: int = 50) -> tuple[float, float, float]:
    """Newton-style descent on residual^2. Returns (theta*, y*, final_residual)."""
    import math
    t, y = theta0, y0
    res = _twist_residual(t, y, eps)
    h = 1e-6
    for _ in range(max_iter):
        # numerical gradient of residual^2
        r = _twist_residual(t, y, eps)
        dr_dt = (_twist_residual(t + h, y, eps) - _twist_residual(t - h, y, eps)) / (2 * h)
        dr_dy = (_twist_residual(t, y + h, eps) - _twist_residual(t, y - h, eps)) / (2 * h)
        # gradient of r^2 is 2r * grad r
        gx, gy = 2 * r * dr_dt, 2 * r * dr_dy
        norm2 = gx * gx + gy * gy
        if norm2 < 1e-30:
            break
        step = 0.5 * r * r / max(norm2, 1e-12)
        t -= step * gx
        y -= step * gy
        t %= 1.0
        y = max(0.0, min(1.0, y))
        new_res = _twist_residual(t, y, eps)
        if new_res < 1e-12:
            return t, y, new_res
        if new_res >= res:
            break
        res = new_res
    return t, y, res


def _twist_map_fixed_point_search(epsilon_grid: list[float], grid_n: int = 200,
                                  tolerance: float = 1e-6) -> NumericalSearchResult:
    """For each ε, scan a grid_n×grid_n grid of (θ,y) seeds, Newton-refine, dedupe.

    Returns: counterexamples_found = list of {eps, fixed_points} dicts where fixed_points has the
    list of distinct fixed points found (the Poincaré-Birkhoff prediction is ≥ 2).
    sup_norm_violations = list of (2 - n_fixed_points_found) per ε; positive means the theorem looks
    violated for that ε at this resolution (would be a real counterexample to PB for this family).
    """
    findings: list[dict] = []
    violations: list[float] = []
    for eps in epsilon_grid:
        # Coarse grid scan
        candidates: list[tuple[float, float, float]] = []
        for i in range(grid_n):
            for j in range(grid_n):
                t0 = i / grid_n
                y0 = (j + 0.5) / grid_n  # avoid boundary y=0,1
                if _twist_residual(t0, y0, eps) < 0.05:
                    t1, y1, r = _newton_refine(t0, y0, eps)
                    if r < tolerance:
                        candidates.append((t1, y1, r))
        # Dedupe — group points within 1e-3 on torus
        unique: list[tuple[float, float, float]] = []
        for c in candidates:
            t, y, r = c
            seen = False
            for u in unique:
                ut, uy, _ = u
                dt = min(abs(t - ut), 1 - abs(t - ut))
                if dt < 1e-3 and abs(y - uy) < 1e-3:
                    seen = True
                    break
            if not seen:
                unique.append(c)
        n_found = len(unique)
        # Filter to interior (PB applies to interior fixed points)
        interior = [u for u in unique if 0.01 < u[1] < 0.99]
        n_interior = len(interior)
        finding = {
            "epsilon": eps,
            "n_fixed_points_total": n_found,
            "n_fixed_points_interior": n_interior,
            "fixed_points_interior": [{"theta": float(u[0]), "y": float(u[1]),
                                       "residual": float(u[2])} for u in interior],
            "pb_predicts_at_least": 2,
            "ok": n_interior >= 2,
        }
        findings.append(finding)
        # "Violation" of PB = fewer than 2 interior fixed points found at this resolution.
        violations.append(max(0.0, 2 - n_interior))
    cert = {"family": "twist_map_fixed_points", "epsilon_grid": epsilon_grid,
            "grid_n": grid_n, "tolerance": tolerance, "findings": findings}
    cert_sha = hashlib.sha256(json.dumps(cert, sort_keys=True).encode()).hexdigest()
    return NumericalSearchResult(
        candidates_checked=len(epsilon_grid),
        counterexamples_found=[f for f in findings if not f["ok"]],
        sup_norm_violations=violations,
        certificate_sha256=cert_sha,
    )


def _check_rational_atomic_invariance(denominators: list[int]) -> NumericalSearchResult:
    """For each denom q, check whether δ_{p/q} (uniform on {0/q, ..., (q-1)/q}) is jointly invariant.
    Per Furstenberg, this works iff gcd(q, 6) = 1."""
    counterexamples = []
    violations = []
    for q in denominators:
        atoms = np.array([k / q for k in range(q)])
        # T_2: 2k mod q; T_3: 3k mod q. Joint invariance ⟺ both are permutations of atoms ⟺ gcd(q,6)=1.
        t2 = sorted(set((2 * k) % q for k in range(q)))
        t3 = sorted(set((3 * k) % q for k in range(q)))
        invariant_under_t2 = len(t2) == q
        invariant_under_t3 = len(t3) == q
        joint = invariant_under_t2 and invariant_under_t3
        violation = 0.0 if joint else 1.0
        violations.append(violation)
        if joint and __import__("math").gcd(q, 6) != 1:
            # genuine counterexample to "joint invariant ⟹ gcd(q,6)=1"
            counterexamples.append({"family": "rational_atomic", "q": q, "stats": {"joint": True}})
    cert = {"family": "rational_atomic", "denominators": denominators, "violations": violations}
    cert_sha = hashlib.sha256(json.dumps(cert, sort_keys=True).encode()).hexdigest()
    return NumericalSearchResult(
        candidates_checked=len(denominators),
        counterexamples_found=counterexamples,
        sup_norm_violations=violations,
        certificate_sha256=cert_sha,
    )
