# Wiring the development-turn methods into the live path

These modules were built and unit-tested during the network-science development program.
None is wired into the live reflex/retrieval path yet — each earns that only by beating the
current method on held-out data, and each live change needs its own guarded test that proves
the guard fires (the owner's rule: a "never" is a spec for machinery, not a promise to be
careful). This note records where each attaches and what its guard must be, so the work is
not orphaned.

## surprise_rank.py -> the reflex surfacing order
Attaches at the point where matched reflexes are chosen for surfacing (the heed gate in the
live graph_fire layer). Replace count/recency ordering with `rank_by_surprise(matched,
fire_counts, total, budget)`. Needs a maintained `fire_counts` (lifetime fires per reflex)
and `total`, extractable from the firing shadow log each dream. Guard + test: a lone
low-information match must still surface (do no harm) — the test already asserts this; the
live wire-in must add a test that a real over-firing reflex is deprioritized but never
dropped when it is the only match.

## calibrate_caution.py -> the firing threshold
Attaches where the fire/no-fire threshold is set. Replace the trailing-window precision
threshold with `caution_threshold(mistake_scores, alpha)`, where `mistake_scores` are the
firing scores on contexts that were confirmed real mistakes (from heed/outcome labels).
Blocked until enough labelled (fired -> heeded -> did-the-mistake-still-happen) outcomes
exist; the heed log has started collecting them. Guard: with no calibration data the
threshold is -inf (warn on everything) — maximum caution, already tested.

## fire.complete_hopfield -> the completion scorer
An alternative to `complete()` inside the network's retrieval. Promote only if the live A/B
(dev_turn1_hopfield_ab.py) shows it fires more AND its usefulness beats the recency baseline
(0.309). If it also fires ~0%, the sparsity is fundamental and the next step is cluster
retrieval (pool the top-M episodes), not a scorer swap. Guard: the k_min direct-evidence gate
must still hold — spread cannot testify.

## fire neighbor cache -> already merged
Behavior-identical speedup (tests pass). Not the live-firing bottleneck (the 156ms per-process
episode load is). Install to the live copy before any latency re-measurement.

## The measured loop these serve
Each promotion follows the loop: measure on held-out, compare against the current method by
more than path noise, record in literal units, wire behind a guarded test. See
`../docs/BRAIN_SCIENCE_AUDIT.md` for the roadmap and the measured findings so far.
