# Diffusion foundations for the brain

Working mathematical document, started 2026-07-23. This grounds the brain's mechanisms —
recall, salience, dreaming, reflex compilation, mistake leakage, audits — in the
mathematics of diffusion models, taking the path-convergence paper as the
foundation stone and connecting it to the modern score-based literature. It is a living
document: each iteration of the diffusion program extends it, and each claim about our
own machinery is checked against the code as it stands on the date written. Companion
artifacts: `eval_score_rerank.py` (the first measured change) and the
foundations program notes.

The paper referenced throughout is *Path convergence in diffusion models: the limit law,
the tail exponent, and the limits of extrapolation* (release repo
`research/path-convergence-diffusion-models`, with an expanded pedagogical volume). Numbering below follows the original manuscript
("Theorem 4.1" etc.), as the expanded volume's atlas does.

## 1. What the paper actually proves

The model is a one-dimensional diffusion sampler built from p stored patterns
x_0^1..x_0^p drawn from a Gaussian target N(0, sigma_T^2). At position x and reverse
time tau in (0, beta], each pattern y gets the weight

    w(y; x, tau) = exp( -(x - y)^2 / (2 tau) + (x_beta - y)^2 / (2 beta) ),

and the sampler's drift comes from the posterior mean

    m_p(x, tau) = sum_nu alpha_nu x_0^nu,     alpha_nu = softmax over patterns of log w,
    v_p(x, tau) = (m_p(x, tau) - x) / tau,

run backward from the fixed endpoint x_beta at tau = beta toward tau = 0 with a shared
Brownian path. The paper's central objects are this finite-p path x^{p} and its
infinite-data idealization x^{infty}, coupled on the same noise.

The results that matter for us, with their honest boundaries:

Theorem 3.1 (exact limit process). As p -> infty the drift becomes affine,
v_infty(x, tau) = -(kappa x + x_beta/beta) / g(tau) with kappa = 1/sigma_T^2 - 1/beta
and g(tau) = 1 + kappa tau, and the population path is exactly a Gaussian bridge:
mean x_beta tau/beta, variance sigma_T^2 g(tau)(beta - tau)/beta, endpoint law
N(0, sigma_T^2). Perturbations propagate by the deterministic flow factor
g(tau)/g(tau'). Boundary: the affine collapse is special to Gaussian targets.

Theorem 4.1 (coupled functional CLT). On every interior strip [tau_0, beta],

    sqrt(p) (x^{p} - x^{infty})  ==>  Xi   in C[tau_0, beta],

where Xi_tau = int_tau^beta g(tau) G(x_r^{infty}, r) / (g(r) r B(x_r^{infty}, r)) dr,
G is the Gaussian empirical-fluctuation field of the weighted patterns and B the
population normalizer E w. Conditionally on the bridge, Xi_tau is centered Gaussian
with a random variance Sigma_tau that depends on where the bridge actually went.
Boundary: fixed dimension, fixed tau_0 > 0; nothing uniform at the endpoint.

Theorem 5.2 with Proposition 5.3 (the tail law). The limit deviation is heavy-tailed:

    P(|Xi_tau| > u) has logarithmic tail exponent theta(tau) = 2 + (beta/sigma_T^2) * tau/(beta - tau),

driven by the mixture mechanism: a rare bridge excursion to height h costs a Gaussian
factor exp(-h^2 / 2 Var) (Lemma 5.1) but inflates the conditional variance
exponentially in h, and the tradeoff of the two exponentials is a power law. Moments
of order q < theta are finite, q > theta infinite; E Sigma_tau =
2 sigma_T^2 g(tau)^2 int_tau^beta (1/r - 1/beta) / g(r)^2 dr, which diverges like
2 sigma_T^2 log(beta/tau) as tau -> 0. Note theta is monotone: theta -> 2 as tau -> 0
(barely square-integrable at the sampling end) and grows linearly in tau/(beta - tau)
early. Boundary: a log-tail exponent, not a sharp asymptotic with a constant.

Theorem 6.1 and Corollary 6.2 (the order of limits). At every FIXED p the deviation on
an interior strip is sub-Gaussian — E exp(a D^2) < infty for some a(p) > 0 — so each
fixed-p far tail dies faster than every power. The heavy tail belongs only to the
p -> infty limit law. The two limits do not commute, and the constant a may degrade
with p; uniform integrability of even the second moment is an open problem.

Theorems 6.4-6.5 and Proposition 6.6 (discrete samplers). At fixed patterns, both the
posterior-mean recursion and the exact categorical sampler (draw one pattern from
alpha, bridge toward it) converge strongly to the same empirical-score diffusion as the
step h -> 0. But at a fixed positive first step the categorical sampler differs from
the posterior-mean update by a non-vanishing term even as p -> infty: the first
selection is uniform over patterns because all initial weights tie. Also the exact
per-step noise variance is h(r - h)/r, not the Euler-Maruyama h.

Theorems 6.8-6.9 and 11.1-11.4 (endpoint obstructions and memorization). Run to
tau = 0 the finite-pattern endpoint is empirical — it lands ON the stored patterns —
hence at total-variation distance one from any nonatomic target; approximating the
endpoint law needs p^{-1/d} covering scale, which kills sqrt(p) endpoint tightness for
d > 2 (6.8-6.9); and
no estimator whatsoever gets expected TV below 1/20 uniformly over a broad density
class (an Assouad/Pinsker argument). Generalization at the endpoint is impossible in
TV; it has to be bought by stopping early or by regularity assumptions.

Theorem 11.5 and Proposition 11.6 (stability under a learned score). If an approximate
drift b~ satisfies || b~(X~_r) - b(X~_r) || <= eps(r) ALONG THE LEARNED PATH, and the
exact drift is ell(r)-Lipschitz, then on shared noise

    || X~_s - X_s || <= int_0^s eps(r) exp( int_r^s ell(u) du ) dr        (Gronwall).

And the hypothesis is not negotiable: Proposition 11.6 exhibits a drift with ZERO
empirical loss on any finite training set that still produces macroscopic path
divergence with positive probability — a smooth bump placed between the training
points, entered by a positive-measure set of Brownian paths. Interpolation on trained
inputs controls nothing off them.

Theorem 4.7, Corollaries 4.8/4.10, Proposition 11.7 (normalization). Empirically
standardizing the patterns (moment matching) acts, at first order, as the L2(pi_T)
projection of the fluctuation field away from the two score directions e_1 = y/sigma_T
and e_2 = (y^2 - sigma_T^2)/(sqrt(2) sigma_T^2). This suppresses pointwise variance by
an exact factor but RETAINS the logarithmic tail exponent; and at finite p it is not
even a pointwise improvement — an explicit two-pattern counterexample makes the
matched estimate strictly worse (errors 0.095 vs 0.013 at the chosen point).

Extrapolation (Sections 7-8). Exact L1/L2 risk curves for shifting an estimate beyond
the data; the probability p_+ that a small push helps tends to 1/2 at central-limit
cutoffs, is provably NOT universally above 1/2 at zero cutoff (Theorem 7.4's
small-variance expansion), and tends to 1 at extreme cutoffs conditionally. Block
results: unions of sub-groups are sample-size-weighted midpoints plus an explicit
second-order correction (Propositions 4.5-4.6, 8.3-8.5), and in the Gaussian location
model NO correction built from block contrasts can improve the grand mean (Theorem
8.6, exact orthogonality).

Verification state, honestly. The 2026-07-17 external audit (recorded and machine-re-verified in
the audit record; report main16_proof_audit_2026-07-17.md) was machine-verified claim by claim and was right about four central defects; all were repaired (the discrete-mean
claim restated as O(h) with a new covariance-perturbation lemma, the curvature sign,
the capture-dip proposition, the extreme-cutoff noise step), the tail lower bound was
closed to unconditional through two hostile referee rounds, and the surviving
frontier is the second capture layer: the exact convergence rate remains a labeled
conjecture and p_+ -> 1 remains conditional on it. Lean is cited as covering 24 cleared-denominator
algebraic identities (that count is unverified here - see Section 9), not the probabilistic arguments. The numerical ensembles had a
duplicated parallel-seed defect, since corrected with stated effective sample sizes.
That is the true shape of the artifact: a strong, audited, mostly-unconditional core
with named open edges.

## 2. The identification: recall is empirical-score denoising

The bridge from the paper to the brain is one line of algebra. Our memories live as
unit vectors on the sphere (bge-small embeddings, 384 dims, ~2400 memories). For unit
vectors, ||x - y||^2 / 2 = 1 - cos(x, y), so the paper's Gaussian pattern weight IS
the softmax-over-cosines readout:

    exp( -||x - y||^2 / (2 tau) )  =  e^{-1/tau} exp( cos(x, y) / tau ),
    alpha_nu(x, tau)               =  softmax_nu ( cos(x, x_0^nu) / tau ).

So the posterior mean m_p is attention over the memory store with temperature tau, and
one step of recall — move the query state toward the weighted memory readout — is one
reverse-diffusion step under the empirical score. This is also exactly the modern
Hopfield update (Ramsauer et al. 2020: transformer attention is Hopfield retrieval
with exponential capacity), so the paper's theory reads directly as a theory of
retrieval from an associative memory:

- Reverse time tau is retrieval sharpness. Large tau: the readout is the corpus mean
  (every memory weighs equally). Small tau: the readout collapses onto the single
  nearest memory. A recall episode is an annealing path from coarse to sharp — the
  same object as the paper's bridge run from tau = beta toward tau = 0.
- Stopping at an interior tau_0 > 0 is generalization; running to tau = 0 is verbatim
  memorization. Theorems 11.1-11.4 make this a theorem: the endpoint is empirical, TV
  distance one from anything smooth. The brain's habit of returning a handful of
  ranked gists plus pointers, rather than one authoritative memory, is the interior
  stop. (In the generative-model literature the same phenomenon appears as the
  memorization transition — Carlini et al. 2023, Somepalli et al. 2023; Biroli,
  Bonnaire, De Bortoli, Mezard 2024 place the collapse time at order log p.)
- The score magnitude ||grad_x log B_p(x, tau)|| — how hard the store pulls at the
  query — is the natural notion of retrieval salience at state x: it is large when the
  query sits in a well-populated basin, near zero in empty regions. log B_p is the
  local log-evidence (free energy) of the query under the memory mixture at
  temperature tau.

The deviation theory then says precisely how finite storage corrupts recall.
Theorem 4.1: the retrieval trajectory deviates from the infinite-experience ideal by
O(1/sqrt(p)) uniformly on interior strips, and the deviation is conditionally Gaussian
GIVEN the context path — its size depends on where the conversation actually went
(B(x_r, r) in the denominator: thin-evidence regions amplify). Theorem 5.2: the
limiting deviation is heavy-tailed with exponent theta(tau) -> 2 near the sampling
end. Lemma 5.1's mechanism — rare context excursions inflate error variance
exponentially — is the mathematical form of "unusual prompts produce
disproportionately wrong recalls."

## 3. The existing machinery, named in this language

What the code actually computes today (verified against source, 2026-07-23):

brain.py deliberate recall. Lexical seeding (2-word index-line overlap, rarity-weighted
fallback) plus a BM25 full-text arm; spreading activation over spread_edges.tsv;
semantic scoring = cosine of each embedded memory to the UNWEIGHTED MEAN of the seed
vectors, recentred by the corpus random-pair baseline ((cos - bias)/(1 - bias), bias ~
0.68 for bge); ranking is bm25-first (measured better than the old 0.625/0.25/0.125
blend on both deliberate ground truths, eval_bm25first 2026-07-15), then a graph tail,
then a semantic tail; supersession chains collapsed onto their endpoints before
ranking.

spread_recall.py proactive hook. Two-hop spreading activation act(n) accumulated as
seed_strength * (w/deg) * DECAY per hop with DECAY = 0.5, HOPS = 2 — in matrix form
two terms of a degree-normalized heat-kernel/lazy-random-walk expansion on the
association graph. Fires only a standout (margin 1.35 over runner-up, activation floor
0.16, salience floor 45), semantically damped by cosine to the seed centroid,
habituated cross-session by max(0.6, 6/n) for surfaced-but-never-read associations.

salience.py. S = 100 (1 - (1 - R)(1 - P)) — a noisy-OR of recency R (Section 6's shipped two-scale
kernel - a fast ~1.3-day arm plus a type-weighted plateau, which replaced the earlier exp(-age/45d) on
2026-07-23) and persistence P (soft-OR of emotional
charge from FEAR/JOY/STAKES lexicons, link centrality, curation, type), with
supersession penalties 0.4/0.7. (Note: this shipped salience S is a stored-memory property - recency
and persistence, no query in the formula - and is NOT the same object as the "retrieval salience" of
Section 2, the query-dependent score magnitude ||grad log B||. Section 2's quantity is the theoretical
ideal; salience.py is the hand-tuned heuristic that ships; this document does not derive one as the
other, and Section 9 lists the identification as open.)

graph_fire (the gut). Each reflex gets a token vector by diffusing activation from its
anchor memories two hops over the same graph (decay 0.5) and projecting onto
vocabulary; firing is a token dot-product against a PER-REFLEX threshold calibrated to
a 2% false-alarm budget on real typical actions (an FDR-style control). A
conscious-to-subconscious buffer lowers a reflex's bar when recall has just surfaced
its scar.

A note on the word "diffusion." The spreading in spread_recall and graph_fire is a discrete graph
operation - a truncated graph heat-kernel / 2-hop lazy-random-walk on the association graph - which is
NOT the same formal object as the continuous score-based diffusion the paper grounds in Sections 1-2.
They are analogous in spirit (both spread mass over a space), but this document does not derive one from
the other, and no load-bearing claim rests on their being identical. What the paper grounds is the
COMPILATION and selection dynamics (Section 4's mapping, Prop 6.6); the graph heat-kernel is the scoring
mechanism the gut runs, and the bridge between the two is an open edge (Section 9), not an established
equivalence.

dream.py. Nightly agenda: consolidate recent, connect similar-but-unlinked, reconcile
stale, wire dangling links, plus random replay; SWS/REM/nap character is set by hour.
The REM pass favors bisociation of distant pairs.

The division into principled and ad hoc is sharp once stated. Measured and defensible:
bm25-first ordering; the 2-hop horizon (3 hops bought +0.0014 MRR, eval_deliberate_recall);
supersession collapse (closed a measured 5/11 stale-premise hole); per-reflex
calibrated thresholds; the embedding bias recentring (verified against random-pair
means); the noise-cluster verdict (eval_denoise: removing 183 status memos moved
recall by exactly 0.000 — the hubs are inert); habituation (attribution-measured
over-surfacing); and, as of 2026-07-23, the recency kernel — the two-scale TAU (a fast
~1.3-day episodic arm plus a measured type-weighted plateau) that replaced the folklore
TAU=45d, fit to the retrieval record and shipped behind eval_recency_ship.py (Section 6),
which carries TYPE_PERSIST at the measured ratios. Ad hoc, in the precise sense that no
measurement or derivation backs the value: DECAY 0.5, ACT_FLOOR 0.16, MARGIN 1.35 (tuned
on n=7 judged fires against a graph that no longer exists — the file itself says so),
SAL_FLOOR 45, STATUS_WEIGHT 0.55, the habituation constants (0.6, 6), edge weights
W_COACT 1.6 vs W_WIKI 1.0, the Read-seed charge 2.2, and the seed-strength arithmetic.
These are dials, and the honest position is that most of them sit inside mechanisms whose
FORM is right (exponential forgetting, noisy-OR persistence, fan-out-normalized
spreading) while their constants are folklore — the recency arm is the first to move from
that column to the measured one.

The three-channel architecture (bm25 head, graph tail, semantic tail) has a structural
justification from the paper, not merely an empirical one. The finite readout m_p is
exactly a convex combination of the stored patterns — strictly positive weights summing
to one (chapter 6) — so it is HULL-BOUND: the readout m_p lies in the convex hull of what seeded it, at any temperature or
under any annealing schedule, so the semantic channel cannot point at - and therefore cannot
preferentially surface - a memory FAR outside that hull (a memory just past the boundary can still rank;
the large cross-topic jump cannot be made). Cross-topic discovery is therefore not a tuning deficiency of the semantic channel
to be fixed by a better centroid or a warmer temperature; it is structurally impossible
for that channel alone, and belongs to the lexical and graph channels, which are not
hull-bound because they do not average. This is why the two-stage annealed readout
(Section 7) failed on the hard subset and why the architecture is factored the way it is:
the coarse cross-topic reach lives in the non-averaging channels by necessity.

Reach is only half of why the architecture is factored; the other half is corroboration. The
three channels fail in independent ways — lexical on shared words, graph on curated relation,
semantic on embedding geometry — so a target near in one may be far in another, which makes them
the recall-side instance of Section 5b's independence condition: a memory surfaced by MORE THAN
ONE channel is corroborated (independent frames agreeing), while a single-channel hit carries the
confident-wrong risk of a lone estimate. The independence is not hypothetical — a node written
this session stayed lexically and graph-reachable while it was still absent from the embedding
sidecar (semantically dark), the channels visibly diverging — so their agreement when the sidecar
catches up is genuine concurrence, not one anchor read three times. Reach needs the channels to
be complementary; trust needs them to be independent, and they are both.

What has been missing altogether is any account of retrieval ERROR. The system
computes point scores everywhere and nowhere a deviation law: no variance attached to
a recall, no tail model for how wrong a surfaced association can be, no bound on the
probability that a reflex misses its family. The paper is precisely a theory of that
missing object — the fluctuation of the finite-store readout around the ideal, its
scaling, its conditional structure, and its tails.

## 4. Mapping table: paper theorem to brain mechanism

| Paper object | Brain mechanism it grounds |
|---|---|
| Pattern weight w(y; x, tau), posterior mean m_p | Recall readout over memory embeddings: softmax attention at temperature tau; the semantic scorer's seed-centroid cosine is its tau -> infty (mean-field) limit |
| Reverse bridge x_beta -> x_0, interior stop tau_0 | A recall episode from query context to retrieved content; gist-level retrieval = early stopping; verbatim single-memory recall = tau -> 0 |
| Population drift v_infty (affine) | The infinite-experience ideal recall direction; what the store approximates at rate 1/sqrt(p) |
| Coupled CLT, field Xi with conditional variance Sigma_tau | Retrieval error from finite experience; conditionally Gaussian given the context path; amplified where evidence B is thin |
| Tail law theta(tau) = 2 + (beta/sigma_T^2) tau/(beta-tau) | Mistakes that get through: rare large recall errors are power-law, worst near the action end (theta -> 2) |
| Fixed-p sub-Gaussian tails + noncommuting limits (6.1/6.2) | Why finite test suites pass (every fixed family is light-tailed) while field behavior at scale shows power-law surprises |
| Excursion cost Lemma 5.1 | Novel contexts inflate error variance exponentially; novelty should buy caution (the danger scalar, currently vestigial) |
| Moment matching = score-direction projection (4.7) + finite-p counterexample (11.7) | Normalization/calibration layers (bias recentring, status weights, salience tilts): asymptotically a projection, not guaranteed pointwise help; measure every one |
| Projection suppresses variance, retains tail exponent (4.8/4.10) | Audits and gates that subtract known failure directions reduce typical error but do NOT change the heavy-tail class of what remains |
| Discrete samplers agree as h -> 0; categorical obstruction at fixed h (6.4-6.6) | Deliberate multi-step recall vs one-shot compiled reflex: compilation is exact only in the small-step limit; a fixed-step compiled habit keeps an irreducible selection error no matter how much experience p accumulates |
| Endpoint is empirical, TV = 1; p^{-1/d} covering (11.1-11.4) | Verbatim memorization at full sharpness; generalization requires early stopping or smoothing; return ranked gists, not one authoritative hit |
| Gronwall stability along the learned path (11.5) | Mistake propagation through a session: score error eps(r) compounds by exp(int ell); place checks close to the act, where deviations have grown |
| Zero training loss controls nothing (11.6) | The knowing-doing gap: a reflex fit to trained wordings has no guarantee on analogous wording — the analogous prompt is a path through the bump between training points |
| Same-noise coupling | Counterfactual evaluation discipline: compare policies on the same event stream (the house already does this in backtests: same-decisions-real-quotes replays) |
| Block midpoint/union laws (4.5, 8.3-8.5); contrasts cannot help (8.6) | Merging multiple agents' estimates (mesh): unions are sample-weighted midpoints plus an explicit second-order term; no reweighting of sub-agent contrasts improves the pooled estimate in the Gaussian case |
| Extrapolation p_+ laws (7.1-8.9) | Predictive-processing bets and curiosity: when pushing beyond what the store supports helps, as a function of how extreme the cutoff is |

## 5. Mistake leakage, quantitatively

Three of the fleet's measured failure modes now have theorem-shaped explanations and,
more usefully, theorem-shaped budgets.

Reflexes that fail to fire on analogous wording. A compiled reflex is a learned score:
it was fit to the wordings in its probe family (zero empirical loss there — the
probes pass 4/4). Proposition 11.6 says this controls nothing between the training
points, and Theorem 11.5 says what does: a bound eps(r) on the score error ALONG THE
REALIZED PATHS — i.e., on the actual distribution of incoming prompts, not the probe
set. The Girsanov form of the same idea (Chen, Chewi, Li, Lu, Salim, Zhang 2023: KL
between the ideal and learned samplers is bounded by the integrated squared score
error along the trajectory, whence sampling is as easy as learning the score) turns
reflex quality into a budget: measure the firing-score margin distribution on LIVE
analogous wordings (the audit protocol already demands "realistic analogous-wording
recall" checks), integrate the squared shortfall, and that is the KL budget by which
deployed behavior can drift from guarded behavior. The 43% reflex coverage of real
failures (ERN ledger, 578 real failures in a rolling 21-day window as of 2026-07-24) was the first empirical eps;
iteration 2 measured the per-reflex version (graph_fire/miss_budget.py, report in
dreaming/MISS_BUDGETS.md, 2026-07-23): scoring every user correction's situation
wording (lesson and reflex clauses excluded) through the production token model at
the calibrated thresholds, the graph channel alone misses 83% of 78 named live
occurrences while holding its calibrated 1.3% false-alarm rate, and the misses
split exactly as Proposition 11.6 predicts - mechanical scars with distinctive
vocabulary fire at positive margin (utf8 surgery, windowless, lookahead, the
after-hours rule), while the broad situational families are token-poor in his plain
words (green_not_trusted 14/14 missed, median margin -4.3, the largest eps^2 entry
at 16.9). The negative control pins the replication (mean false-alarm 1.32% against
the 1.5% budget), and the independent held-out positives confirm the channel scoping
(0/12 fire on the token channel; those cases are the ones the report-surface
structural checks exist for). The bind is now quantified: the token channel cannot
chase the green family without blowing its false-alarm budget, so the next
compilation target is act-keyed, not token-keyed - a guard on the completion-claim
act itself, which is where pre_report already points.

False-green gates. A gate that verifies a projection of the work (the
checker-covered-only-half scars: second_order_blocks_check verifying the first-order
half; the length-only storage gate) is exactly the moment-matching situation: removing
checked directions suppresses variance in those directions and leaves the tail class
of the residual untouched (4.8/4.10). A green gate therefore licenses only the
subspace it measured. The audit convention already in force — name what the checker
actually checks — is the right response; the theorem says there is no clever gate
weighting that escapes it, only enlarging the measured subspace.

Mistakes surviving audits. Every fixed audit family is a fixed-p object: sub-Gaussian,
Theorem 6.1 — within the family, failures are light-tailed and the family soon looks
clean. The deployed system faces the mixture over contexts, where conditional variance
is random and the realized tail is the polynomial theta(tau). The two do not
contradict; they are the noncommuting limits of Corollary 6.2. Practically: passing
n audits shrinks the constant, not the exponent, and the exponent is worst at the
action end — hence checks that bind at the act (report-surface blocks, pre-mutation
asserts) are worth more than upstream checks of the same strength, which is also what
the Gronwall factor says.

The act-proximal law is forced a third, independent way (source read of the tail-law
chapter, 2026-07-23). The balance function P(r) = c_V(r) Var(x_r) — the competition
between the Gaussian cost of a rare excursion and the exponential variance
amplification it buys — is strictly decreasing on (0, beta), so the excursions that
drive the polynomial tail concentrate at the act-proximal end of the trajectory. Three
separate theorems now say the same thing: the Gronwall weighting (an error bound), the
noncommuting limits (an order-of-limits result), and the excursion balance (a rare-event
argument). Three arguments converging is a firmer object than any one
bound - with the honest caveat that all three are corollaries of the SAME diffusion-sampler model, so
by 5c's own independence bar they are closer to one anchor read three ways than to three independent
frames; the genuinely independent datum is this evening's own operator failure (Section 5a), which is
real-world, not model-derived. The guard budget should be spent accordingly.

### 5a. The fourth guard class: conditioning-completeness (input-side)

The three-channel union above guards OUTPUTS — wording (token channel), output
signatures (ERN), and claim surfaces (report-surface checks). It has a structural blind
spot on the INPUT side, demonstrated on the operator mid-program on 2026-07-23: a peer
agent reversibly suspended this session's archive-reader processes and announced it with
ack-required mesh messages; the diagnosis loop sampled logs, process tables, and compute
state but never the inbox, so a suspended process was killed as if wedged. No output
guard can catch a decision conditioned on stale state — the score was evaluated against
an incomplete input, not mis-scored. In the paper's frame this is not a large-tail event
of the readout but a wrong conditioning set, which the CLT says nothing about because it
assumes the conditioning is complete. The remedy is a guard keyed to the ACT and its
inputs, not to any output surface: before a high-stakes irreversible act (kill, delete,
override, deploy), assert the freshness of each standing input channel (mesh inbox read,
compute state read, claims checked) and block or warn on staleness. Iteration 4 reached
the same conclusion from the measured side — its act-keyed green-claim guard fires 0/14
on the mined user-correction corpus and the whole text-guard union catches at most 7/14,
so at least half of the corrections are structurally unreachable by any wording guard.
Three roads — theory, a live operator failure, and the measured correction corpus — meet
at one place: the guards that bind key on acts and inputs, not on wording.

### 5b. The confident-wrong leakage class (why verification, not a faster flag, is irreducible)

The spread signal of Section 7 measures posterior CONCENTRATION, S = 1 - ||m_p||^2, and a flag
on high S catches DIFFUSE recalls — the ones that hedge across many basins. Concentration is
not the same as correctness, and whether it even correlates with correctness is unmeasured here —
the n_eff proxy that tracks wiki-link hit@10 is, per Section 7, probably query breadth. The candidate-
side S-vs-RECALL test has since run and found S null (retired, Sections 7/8.3/9); S-vs-correctness proper
stays unmeasured, and 5b's conclusion below is by construction and does not rest on it. One thing holds by construction regardless of any
correlation: a recall can be concentrated and right (low S, the drivefs case that broke the b_hat
threshold) or concentrated and WRONG (low S on the basin that is nearest but not the intended one),
and on S alone the two are identical (their full posteriors differ - it is the concentration statistic S,
not the posterior, that cannot tell them apart). A spread flag cannot catch the confident-wrong
RESIDUAL — however rare or common it proves to be — because nothing in S distinguishes it from
confident-right; the existence of the residual, not its frequency, is what forces the remedy. This is a THIRD leakage
class, distinct from the two already named: the output guards (Section 5) see only the claim's
surface, which the wrong basin fills just as well-formedly as the right one; and the input-
freshness guard (5a) passes it, because the conditioning set can be complete and fresh — the
error is in the convergence, not the inputs. The only thing that separates confident-right from
confident-wrong is an INDEPENDENT frame: a second retrieval seeded differently, or the deliberate
multi-step layer that re-derives rather than recalls. The one-step compiled reflex cannot do it —
Proposition 6.6's fixed-step obstruction is exactly the statement that a single step keeps
irreducible selection error and carries no second frame to check itself against. So the two-layer
architecture is forced, not stylistic: the gut (one step) scores and can raise diffuseness, but
confident-wrong is caught only by paying for a second, independent evaluation. That is why the
irreducible remedy for the errors that actually get through is verification, not a faster flag —
and why the guard classes are orthogonal by leakage type: output-surface (Section 5), input-
freshness (5a), and cross-frame verification (5b), each blind exactly where the others bind.
One condition makes the verification real: the second frame reads the commit-variance S only if
it is INDEPENDENT of the first. Frames that share seeds or framing land on the same wrong basin
together — one anchor measured N times, no reduction. Only frames that could have diverged (a
differently-seeded retrieval, a different instrument, a different dataset) force a confident-wrong
recall to independently reproduce its exact error to survive, which it rarely does; the residual
falls with the number of TRULY independent frames, not with the number of looks. This is the
theory under the standing bar that "verified" means two instruments that cannot copy each other
agreeing: independence is not stylistic rigor, it is the condition under which a second look
reduces the confident-wrong residual at all.

### 5c. The proxy–referent gap: precision and recall of a compiled reflex are one quantity

Section 5b is the recall side of a more general fact. Every compiled reflex — a spread flag, an
output guard, the conditioning-freshness guard, a surfaced scar — fires on a PROXY it can compute
in one step (concentration S, a claim's surface form, a command's text, a pattern-match to a past
mistake) standing in for a REFERENT the deliberate layer would have checked (correctness, true
harm, the actual scope of an act, whether this situation IS the one the scar names). Compilation is
exactly this substitution: the two-step check that read the referent is distilled into a one-step
reflex reading a cheaper proxy, and the gap between them is the compilation residual. Wherever
proxy and referent DISAGREE the reflex errs, and the sign of the error is set only by the reflex's
polarity. A flag whose action on high proxy is PASS (5b: low S lets a recall through) leaks a
false negative where the referent would have blocked; a guard whose action on a match is BLOCK
(Sections 5, 5a) raises a false positive where the referent would have passed. Precision and recall
of the reflex are not two problems. They are the two signs of one proxy–referent disagreement
region, and a fixed proxy cannot be tightened on one sign without moving the other — thresholding
the proxy trades false positives for false negatives and never leaves the region — unless the
tightening reads the referent more directly.

Iteration 8 measured this region on the block side. The conditioning-completeness guard fires on a
command-text proxy for "act that needs a fresh channel"; over 25–50 transcripts, 59% of its fires
land where the proxy reads act-and-stale but the referent is benign (a scratch-dir scp, a research
restart) — a corrected shadow rate of 24–27% once the command-VISIBLE part of the referent is read
back in. The instructive residual is the git-push fraction, 40–54% of what remains: there the proxy
CANNOT read the referent even in principle, because "git push" carries none of the scope (branch,
remote) that decides whether the push touches shared state. No richer regex closes that sub-region.
It is the precision-side twin of 5b's confident-wrong residual — the place where a better proxy does
not exist and only an INDEPENDENT frame that reads the referent (a git-aware check resolving the
actual remote) can. The independence condition that makes 5b's verification real is the same thing a
guard needs to tighten without either nagging or going blind; one principle governs both signs.

The precision side is not cosmetic, and the reason is a second-order cost the block-polarity gap
carries: an over-firing reflex does not merely annoy, it HABITUATES. The 07-12 heeding audit found
generic reflexes firing 100–246 times a day — a firehose in which the rare relevant fire is buried
and read past, so that detection ran near 80% while only about 5% of caught errors were actually
blocked. Every false fire in the disagreement region spends the channel's own signal; precision is
what keeps a reflex legible enough to be heeded at all. That gives the tightening an already-deployed
form: convert to a BLOCK only where the fire is provable against an OBSERVED referent — the
report_guards stop blocked a class of unobserved dead/broken claims at 0% false-block over 5440 real
messages — and leave the coarse, referent-blind fires as surface. "More detectors make the wall
worse" is the precision/recall identity restated: adding proxy-fires cannot tighten a proxy, only
reading the referent can. And the convergence is itself cross-frame — an empirical firing-rate audit
and a selection-variance derivation, reached from opposite ends, land on the same remedy, which is
the independence condition of 5b operating one level up.

This closes the two failure axes into one account and fixes what to measure. The gap is quantified
on the guard side (the 24–27% corrected band, the 40–54% blind sub-region) and measured this session on the
flag side too: whether the flag's proxy S tracks recall is the candidate-side
S-vs-recall test (its result is below) — the flag-side measurement of exactly this proxy–referent correlation.
The duality predicts the same gap seen twice; both signs are now measured. If S proves to carry no correctness signal beyond breadth, the flag side is pure
gap — a proxy decorrelated from its referent, closable only by an independent frame — while the
guard side is already partially closable from the command text; a difference in degree the single
mechanism should predict and the now-measured number makes concrete: S is NOISE (07-24, n=1607 non-seed, partial coefficient
+0.008 controlling for n_eff, bootstrap 95% CI [-0.011, +0.027] straddling zero), and
corr(S, n_eff) = -0.08, so S is decorrelated from recall AND from breadth - the flag side is PURE
gap, a proxy that does not read its referent at all. That confirms 5b outright: no cheap flag reads
commit-reliability; verification is the only instrument (the selection-variance measurement).
Two measured facts sit near this null but, on inspection, do NOT explain it. The bge store is anisotropic
(||corpus mean|| = 0.823, the ~0.68 bias build_orphan_edges.py removes), so S = 1 - ||m_p||^2 is
range-compressed (std 0.023 over [0.009, 0.232] on the eval_S_rows real-recall rows, 2026-07-24). The
tempting inference - a barely-varying proxy cannot predict - is WRONG: correlation and logistic
significance are SCALE-INVARIANT, so a compressed S could still track recall; the CI-straddles-zero result
is genuine no-signal, not a range artifact. The geometry is a real property of the store but NOT the cause
of the null - 5b's by-construction argument is what carries it. (A whitening transform stays a live lead,
but NOT by 'widening S's range', which by the same scale-invariance changes nothing - only because
whitened-S is a genuinely DIFFERENT statistic whose readout composes different structure; untested.)
Recorded because the geometric story is tempting and false: a checked dead end, kept so it is not
re-proposed.

## 6. Dreaming: the reverse process across the noise scale (SWS and REM)

Sleep consolidation in the diffusion frame: SWS replays the day's episodes at LOW
noise — small perturbations of recent memories, reconstructed into existing structure
(the filer). REM samples at HIGH tau, where distinct memories share basins and the
readout mixes them — bisociation is literally sampling the coarse channel where
cross-domain basins have merged, then annealing the blend down to something concrete.
The current dream.py encodes this qualitatively (SWS = sections A-D, REM = E-G with
distant-pair bisociation, nap = A only). What diffusion theory adds when we take the
next step: a REPLAY SCHEDULE. In score-based training the noise level per sample is
drawn from a schedule chosen so gradient signal is spread across scales; the analogue
here is choosing, per memory, the perturbation scale at which it gets replayed —
recent episodic memories at small tau (consolidation), mid-strength associations at
the tau where their basin boundaries lie (the paper's tau_* = beta sigma_T^2 /
(beta + sigma_T^2), where theta = 3, marks the crossover between variance-dominated
and tail-dominated regimes and is a natural center for "interesting" replay), and a
thin uniform tail of deep-past replay (the anti-forgetting term, already present as
"a dash of random replay"). This is iteration-2 material; it should be built against
a measurable target (does scheduled replay improve next-day recall of consolidated
material over the current heuristic agenda?).

### 6a. The replay schedule, made concrete (PROPOSED, iteration 2 — ships only after the A/B)

A per-memory (stream, tau) draw against a demand curve, three streams with a fitted budget split.
CONSOLIDATION (SWS): sample recent memories with probability proportional to s(m)*K_recency(a(m)) —
the two-scale recency kernel already shipped above IS this stream's recency term — and replay at
small tau, filing the day into existing structure. ASSOCIATION (REM): sample mid-strength memories
whose distance to their nearest OTHER basin puts that boundary near tau_*, and replay at tau ~ tau_*,
the coarse channel where the two basins have merged and the readout mixes them. This sharpens the
current heuristic rather than restating it: dream.py picks bisociation pairs by raw distance, but the
theory says the useful sample is boundary-proximity to tau_* — a pair so distant its basins never
merge within the trajectory is noise, not insight; the pair worth replaying is the one whose boundary
sits at the crossover. ANTI-FORGETTING: a thin uniform tail across the whole store at mixed tau (the
dash of random replay already present). The split and the tau centers are FIT, not guessed, against a
measurable objective — next-day recall of the night's consolidated material plus the rate of valid new
associations, minus forgetting of old material — and the schedule ships only if it beats the heuristic
agenda on that objective, the same pre-registered-gate discipline the recency kernel passed. A design
to measure, not a derivation to wire on faith.

Conscious-to-subconscious compilation is distillation. The deliberate path (read the
scar, reason, decide) is the multi-step sampler; the compiled reflex is a one-step
consistency function trained to map any point of the trajectory to its endpoint
(Salimans & Ho 2022; Song et al. 2023). Two transfers from that literature: first,
consistency training needs trajectory pairs, not endpoints — compile reflexes from
transcripts of the full deliberate episode (the probes that replay wording families
are exactly this), never from the correction line alone. Second, Proposition 6.6's
fixed-step obstruction: a one-step compiled policy retains an irreducible selection
error that more experience does not remove — so the gut's role is to RAISE the issue
(fire, lower the bar, prime), while resolution above threshold belongs to the
deliberate layer. Third, the self-consistency property (Song et al. 2023): a consistency
function maps EVERY point of a trajectory to the same endpoint, so a well-compiled reflex
should return the same verdict whether it is queried at the earliest prompt or the
near-decision state — a reflex that fires only late, near the act, is not self-consistent;
it has failed to map the early trajectory points to the verdict. This composes with Section 5 once RAISING is
distinguished from BINDING. Section 5 is about where the BINDING check belongs — the hard gate that
blocks (a pre-mutation assert, a report-surface block) sits AT the act, where the error tail is worst
and where it sees the real act. Section 6 is about where the RAISE belongs — the reflex must fire its
warning EARLY, since a raise arriving only at the act is too late to change course, and
self-consistency is what makes early firing achievable (the failing trajectory is identifiable at its
early points). Two ends of one pipeline, not rivals: raise early, bind at the act. A reflex that fires
only late fails Section 6 (no early warning); a gate that binds upstream fails Section 5 (it never
sees the act); a complete guard needs both. The test is concrete: replay a
failing episode from its EARLIEST point and confirm the reflex fires there, not only near the
decision (an ERN/act-site guard that only catches the near-final claim is the not-yet-consistent
case). The two-layer design the brain already has is the theoretically
correct factorization; the theory's contribution is to say why neither layer can
absorb the other.

Time is the second annealing axis (measured 2026-07-23; RECENCY_KERNEL.md). The
retrieval-demand kernel is two-scale in age — an episodic component decaying with tau
near 1.3 days and a type-weighted persistent plateau, with the feedback lessons
retrieving at roughly thirteen times the project rate beyond thirty days. This is the
same sharp-head, warm-tail structure the readout study found in similarity space, now
appearing in time, and it makes the replay schedule concrete: sample replay ages from
the measured demand curve rather than uniformly over the recent, center the interesting
replay at the tau-star crossover above, and let the plateau families carry the deep-past
tail they empirically earn. The recency arm of salience is no longer conjecture: the
two-scale form (fast episodic tau plus a type-weighted plateau at the measured ratios)
SHIPPED on 2026-07-23 behind a pre-registered gate (eval_recency_ship.py green on all
arms — positive controls at rank 1 in both kernels, deliberate recall byte-identical,
hard rules still maximally vivid, proactive firing down; BRAIN_RECENCY_V1 rollback). The
45-day exponential it replaced smeared the episodic component roughly thirtyfold and
granted the plateau nothing. What remains for a later iteration is the replay SCHEDULE
itself (dream age-ladder); the salience recency arm is done.

## 7. The first measured change (this iteration)

The semantic scorer is the brain's readout map, and today it computes the tau -> infty
limit: cosine to the unweighted mean of the seed vectors. The paper's exact object at
finite temperature is the softmax readout, and its scalar form for scoring a candidate
memory m against seeds s_1..s_k with strengths p_nu is the weighted log-partition
(free energy) at the candidate:

    score_tau(m) = tau * log( sum_nu p_nu exp( cos(m, s_nu) / tau ) ),   sum_nu p_nu = 1.

As tau -> infty this recovers the current centroid score (up to a monotone map); as
tau -> 0 it becomes max_nu cos(m, s_nu) (nearest-seed retrieval). At moderate tau it
mitigates the centroid's known failure: a query touching two topics puts the mean between
them, matching neither; the free energy scores proximity to EITHER basin (still a convex mixture, so a
small measured gain WITHIN the hull - hit@10 +0.01 on the hard subset - not an escape from Section 3's
structural limit; hence "mitigates", not "fixes"). The paper's
own moment-matching lesson applies to us here — an asymptotically clean improvement
need not help pointwise at finite sample — so the change ships only on measurement:
eval_score_rerank.py runs the established wiki-link protocol (5-fold held-out
hand-authored links, the same ground truth that vetted bm25-first) comparing the
centroid scorer against score_tau across a temperature ladder, component-level and
in-pipeline, with positive controls (known query -> known memory) required to stay
green. Results are recorded in the eval's verdict block and in the program memory
node; the live brain.py changes only if the win is robust across folds, per house
rule.

Measured outcome (2026-07-23, run 2 after an active-row masking fix; 6,165 held-out
wiki-link queries component-level, 2,306 of them semantically hard, 596 pipeline
queries, 5 folds; raw output in the eval's verdict block). The measurement split the
question in two, and the theory's own annealing structure showed up in the data. On
all pairs the sharp free energy dominates: tau = 0.05 lifts component hit@10 from
0.094 to 0.217 and MRR from 0.0463 to 0.1075, positive on every fold — but that
regime is the topical one, where the target sits lexically near a seed, and in the
live bm25-first architecture the bm25 head already owns it. On the hard subset
(target neither a seed nor in the query's bm25 top-40 — the regime the semantic tail
actually serves), the ordering inverts: sharp pooling falls below the centroid (MRR
0.0068 vs 0.0111, negative on all five folds) while the strength-weighted centroid
wins (0.0163, positive on all five folds; hit@10 0.020 -> 0.030), with warm
temperatures (tau 0.2-0.4) statistically indistinguishable from it. Sharpness helps
exactly where retrieval is already easy and hurts where it is hard; the coarse
channel is what reaches across topics, which is the annealing story stated as a
measurement.

Shipped: the strength-weighted seed centroid — the equal-weight to evidence-weight
step of the readout, with no tuned constant (choosing a temperature from this eval
alone would be an argmax on one axis of one ground truth, the move the roll-cadence
audit branded as noise-picking). It beats the unweighted centroid on both regimes on
every fold, leaves the end-to-end pipeline metrics unchanged (MRR 0.1548 vs 0.1548,
n = 596 — bm25 dominates the head, as designed), and passed all six positive
controls live post-patch, each target at rank 1, with BRAIN_SEM_CENTROID=1 verified
to reproduce the pre-patch output exactly. Seed sets run to hundreds of slugs on
ordinary queries (276 on the audited example), which is precisely the dilution
regime where equal votes drown the strong evidence. The temperature ladder is
iteration-2 material: a two-stage annealed readout — sharp head for near-duplicate
retrieval, warm tail for cross-topic association — is what the two regimes jointly
recommend, and it should be built against a ground truth that separates the regimes
by construction. (Iteration 2 built exactly that ground truth and REFUTED the union
form: a per-query union of the sharp and warm channels recovers the topical gain but
loses the hard subset on every fold, because a lexically-unreachable target is near via
the aggregate topic, never via one dominant seed, so nearest-basin sharpness locks onto
the wrong memory. The brain's existing bm25-head-plus-warm-semantic-tail already IS the
annealed readout, factored across channels — the lesson, not a new sampler.)

The evidence-mass flag: explored, calibrated, and DECLINED (2026-07-23). Chapter 7's
exact identity m_p - m_infty = n_hat / B_hat decomposes all retrieval error into a
centered fluctuation over the empirical evidence-mass denominator B_hat, computable at
query time, which motivated a thin-recall flag on small B_hat. Calibrating the EXACT
in-code quantity (B_hat = evidence mass times seed coherence, not the bare strength-sum
proxy tried first — the estimator matters) over real prompts refuted the hard-threshold
form: a genuinely answerable narrow query (rank-1 recall) scores 8.75 while a vague
query scores 7.56, a gap of 1.2, because B_hat conflates few-but-coherent seeds (a
narrow real question) with incoherent seeds (a vague one). Any threshold between them
overfits the controls and false-positives on narrow real questions, so the flag does not
ship. The follow-up hypothesis that seed COHERENCE separates the two was also tested and
falsified in-session (the narrow query's coherence is 0.28, LOW; the vague query's 0.875,
HIGH — inverted). The corrected lead was effective seed count n_eff =
(sum p)^2 / sum p^2, and it survived the scale test that broke the other two. On 1468
non-seed wiki-link targets, hit@10 falls monotonically from 0.391 at low n_eff to 0.079 at
high n_eff (median rank 20 to 98) — but in the direction opposite to the original guess:
concentrated recall (few effective seeds) is reliable, diffuse recall is not. The intuition
had the sign backwards, and only the measurement found it; the run's own summary line even
mislabeled the inverse fall as flat noise, because it tested only whether the curve rose.

Chapter 9 supplies the exact quantity underneath. The categorical selection update equals
the posterior-mean update plus a centered martingale increment whose variance is exactly the
candidate spread S = sum_i alpha_i |Y_i - m_p|^2 (law of total variance; it does not vanish
at finite step). For unit-normalized candidates this collapses to an identity, S = 1 -
||m_p||^2 with m_p the alpha-weighted mean direction: S is one minus the squared resultant
length of the retrieved candidates - a candidate-side coherence deficit, the circular-statistics
1 - R^2. Committing to a recall is noisier than scoring it, by the disagreement among
the retrieved candidates. So the reliability axis is posterior concentration, and it is read
two ways: seed-side by n_eff, which is now measured, and candidate-side by S, the exact
commit-variance, whose own test has since run (resolved below, 07-24). The plumbing (an optional diag dict in
_semantic_scores returning B_hat and n_eff, off by default so recall is unchanged) stays as
the substrate. The measured signal is on wiki-link recall, not on act-commit outcomes, and
n_eff is entangled with query breadth; S, being the coherence of the retrieved BASIN
(downstream of retrieval) rather than a property of the query words, is the check that
separates a real concentration signal from breadth in disguise - it should not inherit the
inversion that killed seed-coherence, which read only how aligned the query WORDS were (a
narrow answerable query seeds few specific, mutually unaligned words; a vague one seeds few
bland similar words). The falsifiable form: an answerable-but-lexically-thin query lands
LOW-S even when its seed-coherence is low. No flag ships before it runs.

The discipline held in an unexpected way: the two flags that measured evidence mass and seed
agreement broke; the survivor's sign came out backwards from the guess; and even the survivor
is provisional - n_eff orders the answerable-versus-vague controls the wrong way (answerable
controls sit near n_eff 5, vague near 3, so the wiki curve would call the vague ones more
reliable), so its wiki monotonicity is probably query breadth until the candidate-side S, the
exact quantity, decides it. Concentration is the axis; the pending S-test settles which
measurement of it to trust.

[RESOLVED 07-24, superseding the "still pending" / "settles which measurement to trust" / "no flag ships
before it runs" language in this section: the candidate-side S-test RAN. S does NOT predict recall
(partial coefficient ~0, bootstrap CI straddles zero, uncorrelated with recall AND with breadth), so S
is RETIRED as noise (Sections 5c, 8.3). Nothing "settled" S; it was killed, and n_eff survives only as
weak breadth. The concentration AXIS and the two-layer conclusion stand; the candidate-side FLAG hope is
dead - which is itself the confirmation of 5b/5c that verification, not a flag, is the remedy.]

## 8. Open problems for the next iterations

Stated as problems, in rough order of leverage; status current as of 2026-07-23 night.
(1) Reflex-miss budgets: measured in iteration 2, extended in iteration 3 (Section 5;
dreaming/MISS_BUDGETS.md); the token channel misses 100% of labeled failing commands
and 83% of correction wordings, proving the gut is a three-channel union with
non-overlapping blindness. Iteration 4 then did the named prerequisite — mined the
14-correction agent-voice green corpus (graph_fire/green_claim_corpus.jsonl) — and the
primary session built the standing shadow eval (eval_guard_against_green_corpus.py) that
grades any guard against it; the act-keyed green guard still fires 0/14 and the text-guard
union caps at 7/14, so at least half is input/act-side (the fourth guard class, 5a).
Iteration 6 widened the mining window (green_claim_corpus_wideK.jsonl, K=5): the
any-in-window union ceiling rises to 8/14, so 7/14 was mildly window-limited but the
input/act-side majority stays unreachable by any text guard. Iteration 7 then BUILT the
fourth guard class (5a): dreaming/graph_fire/conditioning_guard.py, an input-side,
act-keyed, log-only shadow that fires when a proposed act (kill/delete/restart/deploy/
override) meets a stale standing channel (unread mesh inbox for any act; also compute_state
for kill/restart; also an asserted claim for delete/deploy/override; a missing channel
defaults to stale). Causal by construction — it conditions only on the proposed command and
which channels the session has already read. Measured: the 2026-07-23 kill-without-inbox
positive control FIRES (act=kill, stale=[mesh_inbox_read]) and its all-fresh twin is silent;
act-recall 26/26, benign decoys 0/22, benign-misfire 0/2500 on real tool actions (the 5.2%
flagged are all genuine acts, and fire also needs a stale channel, so a session that has read
its inbox and compute-state fires 0% — meets the <1.5% budget). Shadow-ready, NOT block-wired
(that stays gated on a live shadow rate). The self-kill case still fires — text cannot tell
killing my own child from killing a peer's process, named as irreducible (resolution is
log-only plus the deliberate layer per Prop 6.6, never a text exemption that reopens the
07-23 hole). Iteration 8 then MEASURED that rate the safe way — retrospectively and read-only, with no
live-hook edit (retro_shadow_analyzer.py, positive-controlled on the known kill-with-stale-inbox):
across 25 transcripts, ~57% of 263 high-stakes shell acts would fire as configured, mostly git
push / scp / restart on a stale mesh inbox. That is far too high to block, and the per-transcript
freshness reconstruction over-counts staleness so the true rate is lower still — the finding is
that the guard's required_channels are too broad. STILL OPEN: tighten required_channels by act
specificity (a shared-repo or live-target deploy needs the mesh inbox; a throwaway copy to
a scratch host does not; a live-service restart needs compute_state, a research restart does not),
re-measure, and add the shell-aware tokenizer for the written-then-executed-script miss. Do NOT
block-wire until the corrected rate is low and the fires are the ones that would actually have
mattered (the iteration-8 shadow-rate measurement). Iteration 8b (this session) DID the
re-measure: the analyzer extended with a command-text act-specificity classifier drops the shadow
rate from 57-66% to a 24-27% CORRECTED band once command-visibly-benign targets (scratch-dir scp,
research restart) are suppressed; git-push is 40-54% of the residual and its scope is not in the
command text, so the remaining lever is a git-AWARE check (iteration 9), not a richer regex, and the
genuine non-git residual is 11-16% of acts. Section 5c places this: the over-fire and 5b's
confident-wrong leak are one proxy-referent gap at opposite polarities.
(2) STILL OPEN, not built: the replay schedule of Section 6 (the dream age-ladder that
importance-samples replay age from the measured demand curve). Iteration 5 was to build
it but ended before reaching it; only the recency KERNEL that feeds the schedule shipped.
VERIFIED against dream.py (07-24): the current replay is NOT uniform — it already does
concern-overlap prioritization (a Mattar-Daw gain-x-NEED term via token overlap with the day's
recent memories) plus an unconscious-tier draw, salience/affective picks, and orphans. What is
specifically missing is the AGE-importance-sampling from the demand curve: age enters only as the
degraded uniform-over-30d+ fallback, never weighted by the measured age->retrieval-need. So the gap
is one axis (age, from the recency demand curve) to add to an already-prioritized replay — narrower
and more actionable than "not built." SHADOW-EXPLORED (07-24, replay_schedule_shadow.py, read-only): two
naive age-weights — demand-proportional R(age) and its derivative — both concentrate replay on the
NEWEST memories (0-3d ~76-95% of mass) and are identical to each other (40/40 top set). Neither yields
the 6a CONSOLIDATION BOUNDARY (a transition-age peak): for an exponential demand, |dR/dage| is still
monotone-decreasing, peaking at age 0, so a true boundary schedule needs an explicit mid-age BAND
target, not a monotone demand weight. And the concern-overlap main path already favors recent
memories, so a naive age-weight risks REDUNDANCY, not new coverage. The open design is therefore
narrower still: a transition-band replay weight that adds what the concern-overlap path misses (caveat:
the shadow compared against the uniform-old fallback, not the concern-overlap main path). A
transition-BAND (log-Gaussian ~5d, illustrative) then CONFIRMS the shape works — it puts ~75% of
replay mass on 3-30d memories, a mid-age peak distinct from both the current old-uniform (96% on
30-90d) and the naive newest-weight (76-95% on 0-3d). So the constructive answer is a mid-age BAND;
building it — tuning the band center/width and gating it to COMPLEMENT, not duplicate, the
concern-overlap path — is the remaining iteration. Reproduce: dreaming/replay_schedule_shadow.py. BUILT (07-24) behind BRAIN_REPLAY_BAND=1 in dream.py
(section 5b there; OFF by default, so the live nightly is UNCHANGED — compile-checked, deps verified,
band logic shadow-verified, and RUN both ways in-session on 07-24 - env-off skips the block with no band
note (live nightly unchanged), env-on fires and adds 3 mid-age picks - but this is verified from the
run's stderr in session, NOT from a persisted log (no replay-band line is written to any dream log, so it
is a live-run check, not a durable A/B artifact): when enabled it adds up to 3 mid-age band picks to the replay set,
COMPLEMENTING the concern-overlap draw, with a stderr note that it fired. So 8.2 has moved scoped ->
demonstrated -> BUILT-behind-a-flag -> A/B-ready; the remaining work is tuning the band center/width and
an eval-gate (eval_recency_ship-style: does the band replay improve consolidation without displacing
the concern-overlap coverage?) before it could default on.
(3) The deviation-aware recall display: the b_hat hard threshold (declined on the
narrow/vague overlap) and seed coherence (falsified) both broke, but the corrected lead —
effective seed count n_eff — passed its scale test (eval_neff_predicts_recall.py): on 1468
non-seed wiki-link targets hit@10 falls monotonically 0.391 to 0.079 as n_eff rises, so
concentration predicts reliability, with the sign inverse to the first guess. Chapter 9
gives the exact candidate-side form, the spread S = sum_i alpha_i |Y_i - m_p|^2
(commit-variance minus score-variance). STILL OPEN: confirm S on the candidate side — a
different computation than the seed-side n_eff, so it separates concentration from query
breadth — before any flag ships. RESOLVED 07-24 (the box run's rows recovered read-only, the
pre-registered verdict reproduced locally, the selection-variance measurement):
S does NOT predict recall — partial coefficient +0.008 controlling for n_eff, bootstrap CI straddling
zero, and uncorrelated with n_eff (−0.08), so not even breadth-in-disguise. S is RETIRED. The
recall-reliability-flag search ends: b_hat dead, seed-coherence dead, n_eff = weak breadth only,
S = noise — none of the four theory-derived proxies we tried (b_hat, coherence, n_eff, S) reads
commit-reliability. That is CONSISTENT with 5b, not proof of it: 5b argues by construction that no
posterior statistic separates the confident-wrong RESIDUAL (forbidding only a PERFECT flag); this is the
separate empirical finding that four specific proxies failed, which does not prove no cheap proxy CAN
exist. It is the same conclusion Section 5c reaches (stated once there, cross-referenced here), and
together the deductive and empirical arguments make verification, not a flag, the reliable instrument. The diagnostic plumbing stays off by default. (4) Salience constants: the RECENCY arm is now fit
and SHIPPED (two-scale kernel, Section 6); TYPE_PERSIST ratios ship with it. What remains
is fitting the remaining folklore constants (the spread DECAY, the fire MARGIN, the
habituation terms) against the live records. MEASURED this session (not yet fit/shipped): the fire
MARGIN/habituation side via the instinct_fires firehose, a rolling 24h count that grew to ~8,100-9,900
fires/day - ~9,900 as a 7-day average, ~8,100 on a single recent day, so ~2.3-2.8x the 07-12 baseline and
volatile day to day - and is dominated by near-decoration reflexes (utf8_text_surgery x1.0 heed-lift at 527/day)
while the load-bearing secrets_in_git (x7.6) is read past 88% (the firehose re-measurement) - though the ~2.8x is partly COMPOSITIONAL (124 distinct reflexes now vs a top-5-dominated 07-12 set; only ~35% per-reflex on those present at both dates, the rest a broader set), a distinction the sws3 consolidation surfaced;
and the PERSISTENCE folklore against old-age recall, where centrality dominates (hubs recall ~16x, so
c_central may be under-weighted - though that ~16x is LOOKAHEAD-confounded: backlinks are counted on
the current corpus, so a memory recalled at old age and later cited reads as a hub, making the
direction partly reverse-causal; n=31, marginal, verified 2026-07-24), charge validates at the high end, and curation is untestable at 30d+
because the index is recency-biased (the salience-persistence measurement). Both are
MARGINAL reads, not controlled fits; a ship needs the eval_recency_ship pre-registered gates, and the
firehose result is lodged as a damp proposal for the firing-layer owner, not a unilateral
change. (5) The mesh-merge second-order term:
Proposition 4.6's bilinear correction says a naive midpoint of two agents' pooled
estimates is off by an O(1/p) term with known structure, p the pattern count each estimate
pools over. Surveyed the live pooling sites (2026-07-24, reasoning only - the exact constant
needs the paper's Prop 4.6): it is a NON-ISSUE for the brain's current combinations, on two
independent grounds. (a) Large p: recall/salience estimates pool over ~thousands of memories,
so O(1/p) ~ 4e-4 - below any threshold that acts. (b) Agreement, not averaging: the multi-agent
combinations that actually matter - blind-audit convergence ("verified = independent blind +
connected auditors AGREEING", gist_blind_audit_convergence) and delegation (parent VERIFIES/
overrides the worker) - are concurrence or verification, not a naive midpoint of two continuous
estimates, so the Prop-4.6 model does not even apply. The only shape that would trip it is naive
averaging of two SMALL-p continuous estimates (a 3-5 panel averaging scores each formed from few
samples); none is in the live path today, so this closes unless such a pooler is introduced -
in which case use the corrected combine or widen the panel, don't naive-average small p. (6) Growing
the store: the CLT is fixed-d, and Theorem 11.1's sqrt(d/p) initial-slope scale warns that
raising embedding dimension without raising effective pattern count degrades the early
(coarse) phase of retrieval first — relevant before any embedding-model upgrade. (7) Heeding — the reverse direction of the
conscious<->subconscious loop, and the least-built piece. Section 6 covers compilation (a deliberate
check distilled into a fast reflex) and 5c the proxy-referent gap of the compiled reflex; the missing
half is HEEDING — when a fired reflex reaches deliberation versus is read past. The failure side is
now measured: the reflex firehose is ~2.8x its 07-12 size and heed rates run ~12% on the high-lift
reflex (item 4; 5c), so the wall that buries the signal is real and growing. A tempting diffusion
account is score DILUTION — Section 2's empirical score is a signal-weighted average of candidates,
so a flood of low-lift reflex-fires lowers its SNR and averages the high-lift fire out. Labeled OPEN,
not claimed: it requires modeling deliberation itself as denoising-over-reflex-candidates, which
Section 2 (recall over MEMORY candidates) does not establish — reflex-fires are action-loop signals,
not recall candidates, so the transfer needs its own justification before it is theory. The next step
is empirical, not more framing: does cutting the firehose (the #265 damp) actually RAISE the high-lift
reflexes' heed rate — the gate that turns the score-dilution guess into a measured mechanism or
refutes it. FEASIBILITY (tested 07-24): that step is blocked two ways, not one. Beyond needing the
#265 damp to CUT the firehose, the heed instrument itself is too narrow to read the effect —
heed_trend.jsonl carries only 4 reflexes with usable counts even aggregated over 37 days
(secrets_in_git heeded 10%, utf8_text_surgery 49%, windows_backslash_path 75%, ps1_hook_edit 14%), so
a population fire-rate-vs-heed correlation is n=4, noise by the thin-sample rule — and those four do
not line up cleanly (two heavy firers, one heeded 10% the other 49%). So the prerequisite is broader
heed INSTRUMENTATION: the wall's cost on heeding cannot be measured while only four reflexes report a
heed outcome. That, not the score-dilution framing, is the binding next step.

## 9. Independent adversarial review (2026-07-24) and dispositions

This document was written by one author across several sessions and never read by an independent
frame - precisely the verification 5b/5c argues is irreducible. So it was handed cold to a
fresh-context reviewer told to attack it: internal contradictions, overclaimed status, empirical
claims checked against the named artifacts, coverage gaps, voice. (Requested on a different model for
architectural independence; the served model could NOT be confirmed from the run transcript, so the
independence here is fresh-context, not confirmed-different-architecture. The model-agnostic and
strongest part is that the reviewer RE-EXECUTED the artifacts, not just read them.)

Reproduced by direct re-execution (the best-grounded parts; hold the rest to their standard): the
candidate-spread-S verdict (verdict_from_rows.py on eval_S_rows.npy - n=1607 non-seed, partial S coef
+0.008, bootstrap CI [-0.011,+0.027], corr(S,n_eff)=-0.08); the conditioning guard (26/26 act-recall,
0/22 benign decoys, 5.2% on 2500 acts); the shadow rate (57.9% raw, 24.1% corrected, git-push 53% of
residual); the green-claim guard (0/14, 7/14, 8/14); the recency kernel and TYPE_PERSIST 12.9x; the
tau_* algebra. Every named script exists - no phantom citations.

FIXED inline from this review:
- The 8.2 replay-band "END-TO-END confirmed" line overstated: the dual run was verified from the
  run's stderr in session and left NO persisted log, so it is a live-run check, not a durable A/B
  artifact. Corrected to say exactly that - the green-not-trusted standard applied to this session's
  own edit.
- The firehose "~2.8x to ~9,900/day" point figure is a volatile rolling count (~8,100 on a later day,
  ~2.3x); restated as a band, not a point.
- Cut one self-congratulatory clause ("exactly the standard this document holds itself to").

NAMED OPEN EDGES (recorded, not rushed-patched - a careful correction outvalues a hurried one):
- Section 1's foundation-paper audit claims (machine-verified claim-by-claim, Lean 24 identities, two
  hostile referee rounds) are the doc's strongest verification claims and the only ones with no
  artifact cited HERE - the one place its own citation discipline is not applied to itself. The
  artifact is the path-convergence paper's audit trail from prior sessions; it should be linked
  in-doc, not restated on trust. ADDRESSED 07-24: Section 1 now cites the audit record
  the audit record and the report main16_proof_audit_2026-07-17.md
  (the machine-verified-claim-by-claim source); but the Lean-identities count (24) is UNVERIFIED - a
  2026-07-24 source-check found the audit report states the Lean files cannot be reproduced from the
  supplied material, and the only "24" in it is equation (24), unrelated. Treat the 24 as unsourced.
- Section 5 (checks that bind AT the act beat upstream) vs Section 6 (a reflex must fire EARLY) use
  the same "act-proximal" vocabulary for opposite-reading timing advice; the "composes with 5"
  sentence asserts the composition without showing it. They reconcile - a final hard GATE placed late
  and an early WARNING signal are different roles - ADDRESSED 07-24: Section 6 now draws that
  raise-vs-bind distinction explicitly.
- "Structurally impossible at any temperature" (Section 3 convex-hull) vs Section 7 shipping a
  centroid patch that "fixes" a hard-subset miss: the patch moves WITHIN the hull (still a convex
  mixture) so it is not a counterexample, but "fixes" overstates a 1-point hit@10 move on a 97%-miss
  baseline. Soften the verb; state why the win does not breach the claim.
- "Three independent ways" for the act-proximal law (Gronwall, noncommuting-limits, excursion-
  balance) are three corollaries of ONE model's axioms - by 5c's own independence bar that is closer
  to one anchor measured three times than to three independent frames. The genuine independent datum
  is the live operator failure. Re-word "independent."
- "Salience" names two objects never reconciled: the theoretical retrieval-salience ||grad log B||
  (Section 2, query-dependent) and salience.py's S = 100(1-(1-R)(1-P)) (Section 3, memory-only, no
  query) - the latter is what ships and is measured. The theoretical one is introduced once and
  orphaned; reconcile it or mark it as not-the-shipped-object. PARTLY ADDRESSED 07-24: Section 3 now
  marks salience.py as not the Section 2 object; the reconciliation (whether the heuristic approximates
  the ideal) remains open.
- Section 6 is titled "forward noising and the reverse process" but never defines a forward noising
  operator (what corrupts a memory); it delivers only the reverse/retrieval half relabeled. Retitle
  or supply the forward analogue.
- The deepest one: the gut's ACTUAL scoring is a graph heat-kernel / lazy-random-walk diffusion
  (spread_recall.py), a discrete graph-diffusion operator distinct from the paper's continuous
  score-based Gaussian diffusion. The doc uses "diffusion" for both and bridges by proximity, not
  derivation. PARTLY ADDRESSED 07-24: Section 3 now flags this gap in the body (the graph heat-kernel
  is not the paper's score-based diffusion, and is not derived from it); the actual bridge remains open.
- Minor: an unexplained 1468 vs 1607 non-seed count between the n_eff and S tests (likely a day's
  corpus growth - note it); the S verdict is stated twice (5c and 8.3) where the house rule is once.

Update 07-24 (two independent reviews, two architectures). ADDRESSED in place: the early-vs-late timing;
the "three independent ways" claim; Section 6's title; the "fixes"->"mitigates" centroid overstatement
(Section 7); the S-verdict duplication (5c/8.3 cross-referenced); the no-perfect-vs-no-useful distinction
(8.3: S is CONSISTENT-with, not ENTAILED-by, 5b); the 5b "identical posterior" -> "identical on S" (the
full posteriors differ); the 1468/1607 non-seed count (a day's corpus growth); and Section 7's stale
"S-test still pending" language (a superseding RESOLVED-07-24 marker - S ran and was retired). (The
foundation-audit citation gap is closed - Section 1 cites the audit record; the Lean "24" itself stays
unsourced per 8.3.)

MEASURED SINCE (moves an open edge forward): the two "diffusions" are no longer name-only. Graph-recall
neighbors overlap the embedding (pattern-manifold) neighbors ~64x chance (top-10 overlap 0.27,
de-confounded of the embedding-BUILT orphan edges; 88x with them; n=200). The obvious worry - that this
is content-circularity from the human wiki links (a human links two notes because they are content-alike,
which the embedding also measures) - is REFUTED by splitting the surviving edges by provenance: the
BEHAVIORAL co-read edges align with the manifold at 56x chance (n=142, 95% CI 50-62x by bootstrap), the CI
overlapping the wiki
links' 52x (n=1472, CI 50-54x; a per-family metric) rather than falling to chance, so the alignment is
not a wiki artifact. Hence the association
graph approximates the manifold's local geometry and graph recall is a NOISY DISCRETIZATION of the paper's
continuous diffusion (graph-Laplacian ~ manifold-Laplacian, conditional on that alignment). Residual:
co-reads are recall-induced and one recall channel is semantic, so "independent" here is more-than-wiki,
not absolute; and the exact derivation, and the 0.27-not-1.0 gap (good-not-exact), remain.

STILL genuinely open: the two objects both called "salience"; the exact two-diffusions derivation (now
measured-partial, above); that Prop 6.6's fixed-step obstruction is invoked for compiled reflexes ACROSS
that bridge (Sections 4/5b/6/8), so its transfer is assumed, not shown; that Section 5c's "one
proxy-referent region" is heterogeneous (a cheaply-closeable majority to the 24-27% band plus a
structurally-unclosable git-push residual); and - the foundational one a fresh-context review surfaced -
that Section 2's identification is licensed only in HALF: the weight = softmax-over-cosines step is exact
algebra for unit vectors (Section 2's one line), but the paper's distributional limit laws (the sqrt(p)
CLT rate, the theta(tau) tail exponent, endpoint TV=1), derived for a 1-D-Gaussian pattern law, are
carried to the 384-dim SPHERICAL store as descriptions of recall without re-derivation - Section 1's own
boundary notes (Gaussian-specific, fixed-dimension) flag exactly this - "a derivation is
not a measurement," at the foundation (measured 07-24: the store's effective dimension is ~54 by participation ratio - 90% of
variance spans 184 of its 384 PCs, top PC 7.9% - i.e. genuinely high-dimensional, so the 1-D transfer is
NOT rescued by a low intrinsic dimension; the gap is now dimensioned, not closed). These want dedicated
work, not a fast patch.

The disposition follows the document's own thesis: an independent frame found real edges the author
could not see from inside, three were cheap enough to fix on sight, and the rest are named rather than
hidden. That an outside pass was needed at all - after the author judged the document finished - is 5b
in miniature.

## References

Anderson (1982), Reverse-time diffusion equation models. Song, Sohl-Dickstein, Kingma,
Kumar, Ermon, Poole (2021), Score-based generative modeling through SDEs. Chen, Chewi,
Li, Lu, Salim, Zhang (2023), Sampling is as easy as learning the score. Benton, De
Bortoli, Doucet, Deligiannidis (2023), linear-in-d convergence. Salimans & Ho (2022),
Progressive distillation. Song, Dhariwal, Chen, Sutskever (2023), Consistency models.
Ramsauer et al. (2020), Hopfield networks is all you need. Carlini et al. (2023),
Extracting training data from diffusion models. Somepalli et al. (2023), Diffusion art
or digital forgery. Kadkhodaie & Simoncelli (2021), denoising as projection. Biroli,
Bonnaire, De Bortoli, Mezard (2024), Dynamical regimes of diffusion models. Path
convergence in diffusion models (2026) (the foundation paper of this program).
