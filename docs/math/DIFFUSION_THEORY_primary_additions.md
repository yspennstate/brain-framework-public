> MERGED 2026-07-23 into DIFFUSION_THEORY.md sections 3/5/5a/6/7 (section 7 CORRECTED on merge to the flag-declined final state, not the staged [2,14]-threshold draft). This file is now a historical record, not authoritative.

# Staged additions to DIFFUSION_THEORY.md (primary session, 2026-07-23 evening)

These four passages were produced by an independent source read of chapters
6, 7, 8, 11, and 16 during an evening work window. Merge each into
the named section of the main document under a mesh claim; until merged,
this file is the authoritative record of the passages. Full working notes
with the supersession chain: dreaming/READING_NOTES_20260723.md.

## For section 5 (mistake leakage): the third mechanism of the act-proximal law

The claim that checks binding at the act are worth more than upstream checks
of the same strength was stated from two results: the Gronwall weighting,
under which a posterior-mean error of size eta(tau) enters the path bound as
eta(tau)/tau and therefore costs most near the action end, and the
noncommuting limits, under which passing audits shrinks the constant of the
deployed tail but never its exponent. The tail-law chapter supplies a third,
independent mechanism. The balance function P(r) = c_V(r) Var(x_r), which
summarizes the competition between the Gaussian cost of a rare excursion and
the exponential variance amplification it buys, is strictly decreasing on
(0, beta): the excursions that drive the polynomial tail concentrate at the
act-proximal end of the trajectory. Three separate theorems - an error
bound, an order-of-limits result, and a rare-event balance - force the same
design conclusion. A law forced three independent ways is a different
epistemic object than a bound, and the brain's guard budget should be spent
accordingly.

## For section 2 or 3 (the readout): the hull-bound corollary

The finite readout m_p is exactly a convex combination of the stored
patterns, with strictly positive weights summing to one. It is therefore
hull-bound: no readout, at any temperature or under any annealing schedule,
surfaces a memory outside the convex hull of what seeded it. This is the
formal justification for the measured three-channel architecture. Cross-topic
discovery is not a tuning deficiency of the semantic channel; it is
structurally impossible for that channel alone, and belongs to the lexical
and graph channels, which are not hull-bound because they do not average.

## For section 7 (the measured change): the exact-identity form of the evidence flag

The finite-sample identity m_p - m_infty = n_hat / B_hat decomposes all
retrieval error into a centered fluctuation over the empirical evidence-mass
denominator. The denominator is computable at query time - it is the
strength-weighted mass of seed evidence before normalization - and small
B_hat is the principled first-order flag for a thin recall, needing no
dimensional correction and no new constants beyond one threshold. Measured
over 180 real prompts from a prompt archive, the mass distribution is
bimodal with a gap: six percent of prompts seed nothing, the seeded bulk
begins near mass fifteen, and any threshold in [2, 14] flags exactly the
thin tier. The sqrt(d_eff/n_eff) quantity explored first survives only as a
secondary diagnostic; the growing-dimensional law that would ground it is
open (chapter 16), and the identity needs no such grounding.

## For section 6 (dreaming): time is the second annealing axis

The measured retrieval kernel is two-scale in age - an episodic component
decaying with tau near 1.3 days and a type-weighted persistent plateau, with
the feedback lessons retrieving at thirteen times the project rate beyond
thirty days. This is the same sharp-head, warm-tail structure the readout
study measured in similarity space, now appearing in time, and it makes the
replay schedule's design concrete: sample replay ages from the measured
demand curve rather than uniformly over the recent, center the interesting
replay at the tau-star crossover, and let the plateau families carry the
deep-past tail they empirically earn. The salience recency arm should decay
at the measured episodic scale, with persistence carrying the type plateau;
the 45-day exponential smears the episodic component roughly thirtyfold and
grants the plateau nothing.
