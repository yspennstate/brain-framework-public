"""Conformal calibration for the "error on the side of caution" warning threshold.

The owner's standing instruction for mistake-catching is to fire faster and more often,
erring toward caution. "How much caution" should be a controlled quantity, not a
hand-tuned number. Split-conformal prediction (Vovk; Angelopoulos & Bates 2023) gives
exactly that: from a labelled calibration set of past firings, pick the score threshold
that guarantees the miss rate (real mistakes the warning failed to catch) stays at or
below a target alpha, with a distribution-free finite-sample bound.

Inputs are (score, was_real_mistake) pairs from the firing shadow log once heed/outcome
labels exist (fired -> heeded -> did the mistake still happen). The method is exact and
tested here; wiring it to the live reflex gate waits on enough labelled outcomes.

  threshold = caution_threshold(scores_of_real_mistakes, alpha=0.10)
  -> the score cutoff that catches >= 1 - alpha of real mistakes (conformal lower bound)
"""
import math


def caution_threshold(mistake_scores, alpha=0.10):
    """Split-conformal threshold that catches at least (1 - alpha) of real mistakes.

    mistake_scores: the firing scores the engine assigned to contexts that WERE real
                    mistakes (the calibration set of true positives).
    alpha:          tolerable miss rate (fraction of real mistakes allowed to slip).

    Returns the largest threshold t such that, with the conformal finite-sample
    correction, P(score of a future real mistake >= t) >= 1 - alpha. Warn whenever a
    context's score >= t. Erring toward caution means a small alpha -> a low threshold ->
    more warnings.

    The conformal quantile index is floor(alpha * (n + 1)); the threshold is the
    alpha-lower quantile of the mistake scores (so all but ~alpha of real mistakes clear
    it). Distribution-free, assumes only exchangeability of the calibration and future
    mistakes.
    """
    s = sorted(float(x) for x in mistake_scores)
    n = len(s)
    if n == 0:
        return float("-inf")  # no calibration data -> warn on everything (max caution)
    # conformal rank: the k-th smallest score is the threshold; k = floor(alpha*(n+1)),
    # clamped to [0, n-1]. Below that score sit at most ~alpha*(n+1) mistakes.
    k = int(math.floor(alpha * (n + 1))) - 1
    k = max(0, min(n - 1, k))
    return s[k]


def realized_miss_rate(mistake_scores, threshold):
    """Fraction of real mistakes that scored BELOW the threshold (were missed)."""
    s = list(mistake_scores)
    if not s:
        return 0.0
    return sum(1 for x in s if float(x) < threshold) / len(s)


def _selftest():
    import random
    rng = random.Random(0)
    # real mistakes score higher on average; draw a calibration set
    cal = [rng.gauss(0.6, 0.15) for _ in range(500)]
    for alpha in (0.05, 0.10, 0.20):
        t = caution_threshold(cal, alpha=alpha)
        # on a FRESH exchangeable draw, the realized miss rate must be ~<= alpha
        test = [rng.gauss(0.6, 0.15) for _ in range(2000)]
        miss = realized_miss_rate(test, t)
        assert miss <= alpha + 0.03, f"alpha={alpha} threshold={t:.3f} miss={miss:.3f}"
        # smaller alpha (more caution) must give a lower threshold (more warnings)
    assert caution_threshold(cal, 0.05) <= caution_threshold(cal, 0.20)
    # no data -> maximum caution (warn on everything)
    assert caution_threshold([], 0.1) == float("-inf")
    print("calibrate_caution selftest OK: coverage bound holds, more caution -> lower cutoff")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(_selftest())
