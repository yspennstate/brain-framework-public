"""Surprise-weighted surfacing priority.

The firing audit showed the subconscious degrades when high-frequency, low-information
reflexes (a reflex firing on >half of all actions carries <1 bit) drown the rare
high-stakes ones and train the reader to ignore every banner. The fix is to order what
gets SURFACED by information content, not by match strength or recency: a reflex's
priority is its surprise, -log2 p_fire, so a 0.5%-rate reflex (7.6 bits) outranks a
53%-rate one (0.9 bits).

This module is the pure, tested core of that fix. It never deletes a reflex (a
low-information reflex is still correct when it matters - do no harm); it only reorders
surfacing and can drop the lowest-information matches below a bits floor when several
compete for a limited banner budget. A future turn wires it into the live reflex heed
gate behind its own guarded test.

  rank_by_surprise(matched, fire_counts, total, floor_bits=1.0, budget=4)
"""
import math


def surprise_bits(count, total):
    """Information content of one fire: -log2(p). A reflex that never fires is
    infinitely surprising; guard the edges."""
    if total <= 0 or count <= 0:
        return float("inf")
    p = min(1.0, count / total)
    return -math.log2(p)


def rank_by_surprise(matched, fire_counts, total, floor_bits=1.0, budget=None):
    """Order the currently-matching reflex names by surprise (rarest first).

    matched:      iterable of reflex names that matched the current context
    fire_counts:  {name: lifetime fire count} (maintained from the shadow log)
    total:        total fire events (denominator for p)
    floor_bits:   matches below this information content are dropped ONLY when the
                  budget forces a choice; a match that is the only one always surfaces
    budget:       max number to surface (None = all, still ordered)

    Returns a list of (name, bits) rarest-first. Deterministic; ties broken by name.
    """
    scored = sorted(
        ((name, surprise_bits(fire_counts.get(name, 0), total)) for name in set(matched)),
        key=lambda kv: (-kv[1], kv[0]),
    )
    if budget is None or len(scored) <= budget:
        return scored
    # over budget: keep the most surprising up to budget, but never drop a match that
    # clears the floor in favor of one that doesn't
    above = [s for s in scored if s[1] >= floor_bits]
    below = [s for s in scored if s[1] < floor_bits]
    kept = above[:budget]
    if len(kept) < budget:
        kept += below[: budget - len(kept)]
    return kept


def _selftest():
    # ubiquitous reflex (fires 53% of the time) must rank BELOW a rare one (0.5%),
    # and be the one dropped when a 1-slot budget forces a choice.
    counts = {"windows_backslash_path": 5300, "leveraged_etf_is_not_linear": 50,
              "measure_one_before_promising_n": 30}
    total = 10000
    ranked = rank_by_surprise(counts.keys(), counts, total)
    names = [n for n, _ in ranked]
    assert names[0] == "measure_one_before_promising_n", names
    assert names[-1] == "windows_backslash_path", names
    # budget=1 keeps the rarest, drops the ubiquitous
    top1 = rank_by_surprise(counts.keys(), counts, total, budget=1)
    assert [n for n, _ in top1] == ["measure_one_before_promising_n"], top1
    # a lone low-information match still surfaces (do no harm)
    lone = rank_by_surprise(["windows_backslash_path"], counts, total, budget=1)
    assert lone and lone[0][0] == "windows_backslash_path", lone
    # bits are right: 53% -> ~0.9 bits, 0.5% -> ~7.6 bits
    assert 0.8 < surprise_bits(5300, 10000) < 1.0
    assert 7.5 < surprise_bits(50, 10000) < 7.7
    print("surprise_rank selftest OK:",
          [(n, round(b, 1)) for n, b in ranked])
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(_selftest())
