#!/usr/bin/env python3
"""math_sense.py - mathematical memory injection from the populated math brain.

On each user prompt: a cheap lexical gate decides whether the prompt is mathematical
at all (two distinct math anchors required - the general brain's measured polysemy
lesson). Only then is the population store opened and the overlay retrieval run with
strict abstention thresholds. Confident hits are injected as a compact context block:
definitions, lemmas, theorems with their exact statements, so agents connected to the
brain receive the mathematics they are thinking about mid-thought.

Ranking is done here rather than taken from retrieve() because two of the scorer's
channels are wrong for this surface. The graph channel propagates a seed's score to
its neighbours, and one closed cluster of campaign deposits owns enough of the store's
edges to collect that mass on almost any formal query; the symbol channel is pure
notation (subscripts, parentheses, commas) and elects the most notation-dense node
rather than the relevant one. Both are downweighted below, and no more than one
campaign deposit may take a slot - a cap, not a topic filter, because a trigger that
has to guess the subject gets it wrong. Measured at this exact surface.

Trust honesty: population nodes are CURATED_SECONDARY/UNVERIFIED until QA upgrades
them; the block says so. Fail-safe silent on ANY error - this hook must never break
or slow a session that is not doing mathematics. Reads stdin (UserPromptSubmit JSON),
writes injection JSON to stdout. Pure ASCII.
"""
import io
import json
import os
import re
import sys
import time
import unicodedata   # 2026-09-13: two helpers here fold accents; without this they raised NameError into a
# swallowed except, which silently skipped the WHOLE on-topic gate - a behaviour change that reads as a result.

HOME = os.path.expanduser("~")
BUILD_SRC = os.path.expanduser(r"~/math-brain-build/src")
STORE_PATH = os.environ.get(
    "MATHBRAIN_STORE", os.path.expanduser(r"~/math-brain-build/local/population/brain.sqlite3")
)
# 2026-09-11: 1400 held THREE clipped cards until 2026-09-08, when cards started going out whole (the 560-char clip
# was a Codex finding); a whole card is ~600 chars and the hint header ~560, so 1400 held ONE card - measured on the
# fresh bank through the hook: 201 of 208 served blocks carried a single note, and every theorem the ranker put at
# rank 2 or 3 was "omitted whole for the context budget". 2400 = the header plus three whole cards.
# 2026-09-11 (evening): on the locate-the-fact bank a campaign note that HELD the answer ("EXACTLY 1.000 at M = 8",
# 500 characters into its statement) was served as an 80-character excerpt plus its Assumes tail, and the solver
# said UNKNOWN. 320 statement characters minus a 220-character Assumes clause leaves 80; the window cannot reach a
# value the budget never reaches. Statement budget 320 -> 480 and block 2400 -> 3000 so three fuller cards still fit.
MAX_BLOCK_CHARS = int(os.environ.get("MATHBRAIN_MAX_BLOCK_CHARS", "3000"))
MAX_STATEMENT_CHARS = int(os.environ.get("MATHBRAIN_MAX_STATEMENT_CHARS", "480"))
TIME_BUDGET_S = float(os.environ.get("MATHBRAIN_TIME_BUDGET_S", "30"))   # was 2.5 until 2026-09-07: under load the
                          # store load alone passed it and the lane abstained silently; the owner's order is that
                          # the brain always goes in, so the lane now waits (the hook's own timeout is the ceiling)
PREFILTER_K = int(os.environ.get("MATHBRAIN_PREFILTER_K", "300"))
GATE_FLOOR = float(os.environ.get("MATHBRAIN_GATE_FLOOR", "25"))     # on-topic gate, 2026-09-07 (see the block after _select)
GATE_RATIO = float(os.environ.get("MATHBRAIN_GATE_RATIO", "0.45"))   # a note must reach this fraction of the best prefilter score
# 2026-09-11: the floor is a raw BM25 sum, and a BM25 sum grows with the QUESTION. A proof-audit prompt of 1,900
# characters carries a query self-score (sum of idf over its distinct indexed tokens) of about 295, so its best
# node clears 25 without trying (99 of 100 mdlit prompts); a mathematician's one-line question carries a self-score
# of 25-40, and the very node that answers it scores 14-22 - under the floor. Measured through the hook on the 09-03
# fresh bank: theorem served 84.3 pct -> 34.8 pct unnamed, 96.6 -> 38.2 named, silent 6.7 -> 58.4 pct, four days
# after the floor landed, with the health check reading 4 of 8 must-speak probes silent. The floor now scales with
# the question: min(GATE_FLOOR, GATE_FRAC x self-score). On long prompts GATE_FLOOR still binds (0.3 x 295 = 88 > 25),
# so the proof-bank behaviour the gate was tuned on is unchanged; on short questions the gold node passes 88/89
# unnamed and 89/89 named (offline, gate_analysis_20260911.json). Rollback: GATE_FRAC=0 restores the raw floor.
GATE_FRAC = float(os.environ.get("MATHBRAIN_GATE_FRAC", "0.3"))
# 2026-09-12 (his 09-02 "fire less, be more confident"): COVERAGE - the best prefilter score over the query's own
# self-score says how much of the question the store's best match covers. Measured in-process on the fresh bank
# (89 theorems x 2 forms + 40 absent targets): silencing below 0.45 cut absent-target fires 34 -> 30 of 40 with ZERO
# change on the 178 answerable forms (81 / 85 served, 48 / 58 hits); 0.50 cut them to 24 but cost 3 served and 2 hits.
# Curve in state/abstention_curve_rows_20260912.json. 0 disables.
# LANDED AT 0.45 AND REVERTED WITHIN THE HOUR (2026-09-12 02:1x): the fresh bank held (0.764 / 0.933, absent fires
# 0.775 -> 0.675) but the locate banks collapsed - walk counts 0/40, open problems 19/40, two-fact pairs 0/30 - and the
# health check failed: a long enumerated question covers little of itself with one row by construction. Default 0
# (off); the dial and the curve stay for a coverage signal that is measured on EVERY bank before it lands.
# SECOND FORM (02:3x, measured on EVERY bank in-process first - state/abstention_curve_allbanks_20260912.json): the same
# 0.45 applied only when the query's self-score is at most 60 keeps the fresh bank (81 / 85 served, 48 / 58 hits), the
# walk counts 40/40, the open problems 40/40 and the pairs 29/30, and still cuts absent-target fires 34 -> 30 of 40.
# Self-score medians: theorem questions 26-37, absent targets 42, locate questions 70-113 - the length of an enumerated
# question is what made the first form blind to it.
COVERAGE_MIN = float(os.environ.get("MATHBRAIN_COVERAGE_MIN", "0.45"))
COVERAGE_QSELF_MAX = float(os.environ.get("MATHBRAIN_COVERAGE_QSELF_MAX", "60"))

# Ranking constants, measured at this surface on 2026-08-22 over three populations
# (126 stratified benchmark probes, his own math-gated prompts, and campaign claims
# self-retrieved from their own bodies with the title held out).
GRAPH_WEIGHT = 1.0        # RE-MEASURED 2026-09-01 on the post-rs store: source-excluded
                          # depends_on eval (60 pairs, hook rank formula): gw=1.0 vs 0.0
                          # doubles top-1 (15% vs 8%), top-5 52% vs 35%, median rank 5 vs
                          # 7, top-50 misses 0 vs 14; famous-query negative control: 0 rs
                          # intrusions in 36 top-3 slots under BOTH weights. The 08-22
                          # zeroing answered the fpp closed-cluster pathology; the typed
                          # rs edge layer inverts the measurement. Rollback: set 0.0.
                          # (was 0.0 2026-08-22..2026-09-01; 1.0 implicitly before that)
SYMBOL_WEIGHT = 0.1       # scales the channel's own 1.35, giving an effective 0.135
# The two abstention thresholds. Both are now readable from the environment SO THEY CAN BE MEASURED, with the
# defaults byte-for-byte the standing values - unset, the hook behaves exactly as before (2026-09-13). The near-tie
# margin is on the owner's own do-not-land list, so this is a measurement door, not a change: it lets a paired run
# say what a different margin would cost and buy before he rules on it. Never ship a non-default value in a launcher.
MIN_SUPPORT = float(os.environ.get("MATHBRAIN_MIN_SUPPORT", "0.5"))   # re-applied to the re-ranked order
MIN_MARGIN = float(os.environ.get("MATHBRAIN_MIN_MARGIN", "0.1"))
CANDIDATE_POOL = 100000   # rank the whole store, then cut - retrieve()'s own limit
SLOT_WINDOW = 40          # would cut on the pre-rerank order and hide the answer
MAX_CAMPAIGN_SLOTS = 1
#: score margin by which a campaign deposit must beat the best non-campaign node to
#: keep its rank instead of being demoted. Swept 2026-08-30: at 1.0 the inversion rate
#: is zero on campaign-drawn AND famous-theorem queries, and the campaign share of
#: slots on famous queries is unchanged at 4.6 percent.
DEMOTION_MARGIN = 1.0

CAMPAIGN_PAT = re.compile(
    r"fpp|first-passage|first passage|percolation|rademacher[ _]singularity",
    re.IGNORECASE,
)  # 2026-09-01: rs cluster added to the cap alongside fpp - pure safety; the famous-query
   # negative control measured 0 rs intrusions even uncapped, and the cap costs rs nothing
   # on its own topical queries (a cap, not a topic filter, as the original note says)

MATH_ANCHORS = re.compile(
    r"\b(theorem|lemma|corollary|proposition|proof|prove|definition|axiom|conjecture|"
    r"integral|derivative|matrix|matrices|eigenvalue|eigenvector|group|subgroup|ring|field|"
    r"vector space|topolog\w*|manifold|measure|sigma.algebra|lebesgue|banach|hilbert|"
    r"probabilit\w*|martingale|markov|random variable|distribution|variance|convergen\w*|"
    r"polynomial|prime|integer|modul(?:o|ar)|congruen\w*|graph coloring|combinatori\w*|"
    r"permutation|isomorphi\w*|homomorphi\w*|continuous|compact|bounded|norm|metric space|"
    r"lipschitz|perceptron|neural network capacity|latin square|orthogonal array|"
    r"cardinalit\w*|countable|bijection|injective|surjective|supremum|infimum|"
    r"inequality|bound|entropy|isoperimetr\w*|geodesic|curvature|homology|fundamental group|"
    r"anticoncentration|antichain|hypercube|partition function|expander|vc.dimension|"
    r"sine|cosine|holomorphic|meromorphic|differentiable|fixed.point|corollary|"
    r"if and only if|counterexample|almost everywhere|measure zero|commutative|associative|"
    r"euler|gauss|riemann|hilbert|banach|cauchy|fourier|laplace|lebesgue|dirichlet|"
    r"noether|galois|erdos|kolmogorov|poincare|brouwer|frobenius|lagrange|legendre|"
    r"fermat|bernoulli|chebyshev|minkowski|weierstrass|bolzano|dedekind|cantor|godel|shannon)\b",
    re.IGNORECASE,
)
LATEX_RE = re.compile(r"\$[^$]{2,}\$|\\(?:frac|sum|int|forall|exists|mathbb|sqrt)\b")

# Mathematics written as plain prose still carries notation, and the gate used to see
# only LaTeX. Measured 2026-08-22 on 37 one-per-family probes and 400 of his own
# prompts: three families whose correct claim ranked FIRST were refused before
# retrieval ran (a dominated-convergence question saying "integral" but never
# "theorem"), while adding this signal fires on 5 more of his prompts in 400 - 1.2%,
# about a second per hundred - and those five are his own campaign questions.
ASCII_MATH_RE = re.compile(
    r"[A-Za-z]_\{?[0-9nkij]\}?|[A-Za-z0-9)]\^\{?[0-9nkpq]\}?|"
    r"\bE\[[^\]]{1,40}\]|\bP\([^)]{1,40}\)|<=|>=|!=|->|\bsum_|\bint_|\blim_"
)


STRONG_ANCHORS = re.compile(
    r"\b(theorem|lemma|corollary|axiom|conjecture|holomorphic|meromorphic|"
    r"euler|gauss|riemann|hilbert|banach|cauchy|fourier|laplace|lebesgue|dirichlet|"
    r"noether|galois|erdos|kolmogorov|poincare|brouwer|frobenius|lagrange|legendre|"
    r"fermat|bernoulli|chebyshev|minkowski|weierstrass|bolzano|dedekind|cantor|godel)\b",
    re.IGNORECASE,
)

# 2026-09-11: the gate refused 44 of the 54 fresh-bank questions the hook was silent on - "Sylow theorems" (the
# strong list has no plural), "Talagrand's convex distance inequality", "Marchenko-Pastur law", a graph-theory
# question with no anchor word at all. The strong list knew analysis and a few probabilists; combinatorics, algebra,
# geometry, statistical mechanics and optimisation had no names in it. Additions were drawn from the eponyms the
# store's own titles carry and measured on 33,780 archived prompts by the GREEK_ANCHORS discipline: the strong set
# adds 27 fires (+0.08 pp; 19 of them his own prompts about theorems in papers, the rest Marchenko-Pastur,
# sub-Gaussian, Riesz, Jensen, Rademacher, one "Cramer"), the weak set 9 more (+0.03 pp; "edges"/"vertex" in
# brain-graph and column-list prompts - the cost of the two-anchor rule). Rejected on the same measurement: "warning"
# (45 fires, none Chevalley-Warning), "abel" (his typo for able), "holder" (the English word). Fresh bank: the gate
# passes 62 -> 85 unnamed, 72 -> 88 named of 89; the six must-stay-silent probes stay silent.
STRONG_ANCHORS_2 = re.compile(
    r"\b(theorems|lemmas|corollaries|conjectures|axioms|"
    r"sylow|burnside|cayley|tur[aá]n|ramsey|menger|kuratowski|urysohn|tychonoff|tietze|zorn|dilworth|sperner|"
    r"k[oö]nig|erd[oő]s|ko-rado|szemer[eé]di|lov[aá]sz|kruskal|katona|"
    r"hoeffding|azuma|mcdiarmid|talagrand|bernstein|chernoff|hanson-wright|paley-zygmund|efron-stein|"
    r"borel-cantelli|khintchine|lindeberg|berry-esseen|slutsky|donsker|skorokhod|prokhorov|"
    r"girsanov|feynman-kac|fokker-planck|galton-watson|cram[eé]r|sanov|varadhan|"
    r"marchenko|pastur|tracy-widom|wigner|semicircle|"
    r"cheeger|alon-boppana|spielman|benjamini|schramm|kesten|fkg|reimer|peierls|griffiths|"
    r"sherrington|kirkpatrick|hopfield|"
    r"jensen|hölder|fatou|fubini|tonelli|egorov|lusin|radon-nikodym|riesz|plancherel|parseval|"
    r"hahn-banach|banach-alaoglu|banach-steinhaus|krein-milman|arzel[aà]|ascoli|stone-weierstrass|"
    r"picard|gronwall|lipschitz|sobolev|rellich|kondrachov|sard|morera|liouville|casorati|"
    r"heine-borel|bolzano|darboux|dini|"
    r"nakayama|wedderburn|nullstellensatz|schur|perron-frobenius|cayley-hamilton|gram-schmidt|cholesky|"
    r"hensel|chevalley|mordell|hasse|goldbach|collatz|"
    r"gauss-bonnet|hopf-rinow|bishop-gromov|cartan-hadamard|gromov|hodge|betti|borsuk|ulam|brouwer|kakutani|knaster|tarski|"
    r"hartman|grobman|poincare-bendixson|sharkovskii|oseledets|"
    r"johnson-lindenstrauss|sauer-shelah|glivenko|cantelli|rademacher|vapnik|"
    r"kkt|karush|kuhn-tucker|farkas|lagrangian|"
    r"neyman|rao-blackwell|lehmann|scheff[eé]|"
    r"l[oö]wenheim|skolem|goodstein|paris-harrington|cook-levin|savitch|courcelle|"
    r"brownian motion|ising model|riemannian|hypercontractiv\w*|percolation|boolean function\w*|algebraically closed|"
    r"abelian|complementary slackness|quadratic reciprocity|spontaneous magneti[sz]ation|first-passage|fpp|"
    r"sub-?gaussian|quadratic forms?|martingale\w*|stopping times?|arithmetic progressions?|"
    r"harmonic function\w*|elliptic equations?|hypoelliptic|sigma-algebra|pi-system|lambda-system|"
    r"large deviations?|rate function|convex bod(?:y|ies)|chromatic number|independence number|"
    r"projective curve|rational points|number field|perceptron|"
    # 2026-09-11 evening: the store carries 359 "Open problem:" ledger nodes and the gate had no word for them - a
    # locate bank asking where a ledger problem is posed was refused on 27 of 40 questions. Measured on 33,789
    # archived prompts: +119 fires (+0.35 pp), the sample entirely his own open-problem goals - the prompts where
    # this brain should speak.
    r"open[- ]problems?|unsolved problems?)\b", re.IGNORECASE)
# 2026-09-12 (goal 30h, item 2): a 30-question owner-voice bank about the probability campaign's OWN results was
# silent on 18; 16 of those were this gate refusing before retrieval, most with NO anchor at all - "is the Aldous-Janson
# ansatz 20 true", "Hasebe Ueda Wang problem 4.7 ...", "Bradley question 7 ...", "is tau star O(n log n) over
# arc-transitive graphs". Two shapes were missing: a NUMBERED CITATION (problem 4.7, question 17.18, ansatz 20,
# conjecture 4.2 - the way a mathematician names a problem in a source) and the probability vocabulary the strong list
# lacked ("brownian motion" was there, "brownian" alone was not; nothing for unimodal, arc-transitive, spectral gap,
# ergodic, mixing, additive functionals, normal approximation, beta-splitting). Measured on 33,845 archived prompts by
# the GREEK_ANCHORS discipline (lanes/m1_probstore/measure_anchors.py, ANCHOR_MEASUREMENT.txt): the whole set adds
# ONE fire to the 19,155 prompts the gate refuses (a random-matrix prompt), the six must-stay-silent probes stay silent,
# and it rescues 15 of the 16 refused owner-voice questions. Landed in two edits (the 2026-09-12 rule): default OFF
# first, then measured ON against OFF on a quiet machine, sequential, same store - fresh bank 71/89 + 84/89, absent
# fires 28/40, long prompts 74/100, notes 170, health 11/6: IDENTICAL on every aggregate and on every item's hit and
# rank (six items differed only in card order or block length, the hook's clock-dependent branches); owner-voice
# served 12 -> 24 and result nodes 10 -> 19 of 30, nothing lost. Default flipped ON 23:2x. MATHBRAIN_ANCHORS_3=0 restores.
ANCHORS_3_ON = os.environ.get("MATHBRAIN_ANCHORS_3", "1").strip() not in ("0", "", "off", "false")
STRONG_ANCHORS_3 = re.compile(
    r"\b(?:(?:open\s+problem|problem|question|conjecture|ansatz|hypothesis)\s+\d+(?:\.\d+)*|"
    r"brownian|unimodal\w*|(?:arc|vertex|edge)-transitive|spectral gap|(?:relaxation|mixing|cover|hitting) times?|"
    r"(?:phi|alpha|beta|rho)-mixing|strong(?:ly)? mixing|ergodic\w*|stationary (?:measure|distribution|law|sequence)s?|"
    r"additive functionals?|normal approximation|beta[- ]splitting)\b", re.IGNORECASE)
# weak: two of these (or one plus notation) open the gate, like MATH_ANCHORS; never alone
WEAK_ANCHORS_2 = re.compile(
    r"\b(vertex|vertices|edges|clique|planar|subgraph|bipartite|monochromatic|colou?rings?|labelled trees|"
    r"subsets|shatter\w*|orbits?|fixed points?|cyclic groups?|finitely generated|ideal|almost every|"
    r"locally integrable|invertible|normal space|closed sets|tangent space|exponential map|multipliers|"
    r"constrained optim\w*|change of measure|density process|couplings|spin correlations?|contours|passage time|"
    r"storage capacity|random patterns|influence|euclidean|pairwise distances|primes|integers|"
    r"empirical distribution|moment generating|gaussian process|direct sum|finite group|finite set|"
    r"hyperbolic|fundamental groups?|isometric|collinear|solvable)\b", re.IGNORECASE)



# --- candidate additions (gate defect fix) ------------------------------------
# The \b before the subscript variable is load-bearing. Without it, `fb_in1k` inside a
# model name like DeiT-Base-Dist.patch16_224.fb_in1k reads as "b subscript i", and so do
# ft_in1k and mae_in1k. Measured on 19,093 of his own prompts, the unanchored version put
# 960 extra fires (+5.03pp) on traffic that was almost never mathematics. Anchored, the
# same fix costs +0.85pp and the new fires are mathematical. A mathematician writes
# single-letter variables (X_1, a_n, S_n); snake_case identifiers have multi-letter
# segments, and a word boundary separates the two cleanly. DO NOT REMOVE THE \b.
STRUCTURAL_MATH_RE = re.compile(
    r"\b[A-Za-z]_\{?[0-9nkijmp]\}?\b"
    r"|\b[A-Za-z]\^\{?[0-9nkpqd]\}?\b"
    r"|\bE\[[^\]]{1,40}\]"
    r"|\bP\([^)]{1,40}\)"
    r"|\bsum_|\bint_|\blim_|\binf_|\bsup_"
    r"|\|\|[^|]{1,20}\|\|"
    r"|<[A-Za-z][^>]{0,20},[^>]{0,20}>"
)

# `convergen\w*` never matched the base verb - "converge", "converges", "converged" all
# missed while only "convergence"/"convergent" hit. Plus the probability idioms the
# original list omitted ("almost surely" was absent while "almost everywhere" was there).
EXTRA_ANCHORS = re.compile(
    r"\b(almost surely|i\.i\.d|iid|in distribution|in probability|"
    r"inner product|expectation|subsequence|converg\w*|poisson|erlang(?=\s+(?:mixture|distribution)\b)|mmpp|"
    r"phase.type|convexity|convex(?=\s+(?:in|function|combination|hull|cone|set|optimization)\b)|concavity|concave(?=\s+(?:in|function)\b)|second moments?|random sums?)\b",
    re.IGNORECASE,
)

# Greek letters SPELLED OUT are a strong signal that prose is mathematical, and the anchor
# list had none - so "at which M is the engine's inequality tight for chi = 1/3, and what
# value must beta take there" carried ONE anchor and was refused before retrieval ran, while
# retrieval with the gate lifted returns a rank-1 hit at 8.75.
#
# WHICH LETTERS, decided by measurement on 28,116 archived prompts rather than by taste,
# because on THIS machine most of the alphabet is not mathematics:
#
#   all 21 names          +21.95pp, 6,171 new fires - alpha, beta, gamma, delta, theta, rho,
#                         sigma and vega are the options Greeks and saturate the trading
#                         traffic; the new fires were loop ticks and sleep instructions
#   minus options Greeks   +0.25pp, 70 new fires - still bad, and for a reason no
#                         hand-written negative would produce: "eta" matches ETA, so a
#                         customer-support assistant prompt lit the gate 21 times. "nu" and
#                         "xi" fired on non-ASCII table text rather than on mathematics.
#   this set              +0.078pp, 22 new fires, ALL of them mathematical - his own
#                         math-brain loop prompts, an RMT bibliography check, an RMT
#                         ensemble review, and the Ising perceptron capacity at kappa != 0
#
# That is an eighth of the +0.85pp the last widening cost. Same discipline as the \b lesson
# above: hand-written negatives passed there too, and only real traffic found the defect.
GREEK_ANCHORS = re.compile(
    r"\b(chi|kappa|mu|phi|psi|omega|epsilon|zeta|iota|upsilon)\b",
    re.IGNORECASE,
)

# Enumerative combinatorics had no anchor at all. The H1 test of 2026-09-05 (60 private
# self-avoiding-walk counts only the store holds) found the plain question form an agent
# would type - "How many self-avoiding walks of length 10 on the square lattice Z^2 start at
# (0,0), stay in the strip 0 <= y < 3, and avoid the sites (-4,2)?" - refused by this gate
# before retrieval ran (one structural notation, zero word anchors), while cli_query with the
# gate bypassed served the right block; the served H1 form passed only because it said
# "exact integer". Measured 2026-09-05 22:4x on 33,278 archived prompts (86.7 MB) by the same
# discipline as GREEK_ANCHORS: 538 prompts carry one of these words, 537 already fired, and
# each candidate set added ZERO new fires - a single weak anchor still needs notation or a
# second anchor, which is what keeps "enumerate the files" silent. The corpus did NOT contain
# a prompt with two of these words and no mathematics ("enumerate the files ... rename the
# lattice config"); the health check's negative case did, and it fired - so these words open
# only the anchor-plus-notation path in _math_gate (see there), never the two-anchor path.
# Never promote these to MATH_ANCHORS or STRONG_ANCHORS.
ENUMERATION_ANCHORS = re.compile(
    r"\b(self.avoiding|lattice|random walk\w*|enumerat\w*|generating function\w*)\b",
    re.IGNORECASE,
)

# A prompt that carries an argument the reader is checking, rather than a question to look up: three or more
# numbered steps, or the words that name the act of checking one. Used only by the kind preference below.
ARGUMENT_SHAPE = re.compile(
    r"(?:(?:^|\n)\s*\d+[.)]\s.*){3,}|\b(first invalid step|is the proof correct|audit the (?:numbered )?argument"
    r"|which step (?:is|fails)|find the error in the proof)\b",
    re.IGNORECASE | re.DOTALL,
)


def _math_gate(prompt):
    """One STRONG anchor (names, theorem-class words), two weak anchors, one weak
    anchor plus notation, or - new - two distinct STRUCTURAL notations with no word
    anchor at all. That last path exists because mathematics written in symbols was
    invisible to this gate: a Cauchy-Schwarz question that never says "Cauchy", and the
    bench's two silent probes, all carried zero word anchors. It deliberately does NOT
    use ASCII_MATH_RE, which contains <=, >=, != and -> and would light up on code.
    The gate is a cost filter; retrieval's abstention thresholds remain the correctness
    gate."""
    if STRONG_ANCHORS.search(prompt) or STRONG_ANCHORS_2.search(prompt):
        return True
    if ANCHORS_3_ON and STRONG_ANCHORS_3.search(prompt):
        return True
    anchors = set(m.group(0).lower() for m in MATH_ANCHORS.finditer(prompt))
    anchors |= set(m.group(0).lower() for m in EXTRA_ANCHORS.finditer(prompt))
    anchors |= set(m.group(0).lower() for m in GREEK_ANCHORS.finditer(prompt))
    anchors |= set(m.group(0).lower() for m in WEAK_ANCHORS_2.finditer(prompt))
    if len(anchors) >= 2:
        return True
    # An enumeration word opens only the anchor-plus-NOTATION path, never the two-anchor one:
    # "enumerate the files ... rename the lattice config" carries two of them, and "the lattice
    # config is missing a field" carries one plus the polysemous "field" - both must stay silent
    # (caught by hand-written negatives at 23:0x, not by the corpus, which held no such prompt).
    # The H1 question carries "self-avoiding"/"lattice" AND "<=", so it passes here.
    if len(anchors) >= 1 or ENUMERATION_ANCHORS.search(prompt):
        return (LATEX_RE.search(prompt) is not None
                or ASCII_MATH_RE.search(prompt) is not None)
    return len(set(m.group(0) for m in STRUCTURAL_MATH_RE.finditer(prompt))) >= 2


def _node_meta(node):
    return node.metadata if isinstance(node.metadata, dict) else {}


def _read_records(store):
    """claim id -> the latest recorded read of it (tools/record_review.py), from the evidence table.

    One query over a small table at hook time. Returns {} on any error so a broken evidence table
    can never silence the hook. The record is shown beside the hint; it does not change the trust
    tag's verdict, because an agent's read is a discovery report and not an authority (the store's
    own promotion rules say so), and a human's single read makes a claim provisional at most."""
    try:
        rows = store.connection.execute(
            "SELECT claim_id, payload_json, created_at FROM evidence ORDER BY created_at"
        ).fetchall()
    except Exception:
        return {}
    out = {}
    for row in rows:
        try:
            payload = json.loads(row["payload_json"])
        except Exception:
            continue
        kind = str(payload.get("kind") or "")
        if kind not in ("discovery_report", "human_proof_review"):
            continue
        out[row["claim_id"]] = {
            "when": str(row["created_at"] or "")[:10],
            "who": str(payload.get("verifier") or "?"),
            "passed": bool(payload.get("passed")),
            "human": kind == "human_proof_review",
            "summary": str(payload.get("summary") or ""),
        }
    return out


def _trust_tag(meta, read=None):
    """How far to trust one served node, read from its own metadata. Nothing in the store carries an
    independent verification event (verification_events was empty on 2026-09-02, and every harvested
    draft has statement_fidelity_reviewed=0), so the tag says what KIND of thing this is and where it
    came from - never that it is right. Owner order 2026-09-02: a note can mean something good or bad,
    not necessarily good; the brain gives a hint. Fail-safe: any error -> 'unverified'.

    `read`, when given, is the claim's latest recorded read (see _read_records): it is appended as a
    dated fact - who read it and what they found - and the closing word stays 'unverified' unless the
    read was a human's, in which case it reads 'provisional'; the store promotes to verified only on a
    second, independent route."""
    try:
        ck = str(meta.get("claim_kind") or meta.get("kind") or "").lower()
        parts = []
        if ck in ("theorem", "lemma", "proposition", "corollary"):
            parts.append(ck + (" with a proof text on file" if meta.get("proof_text") else ", no proof on file"))
        elif ck:
            parts.append(ck + " lead")
        parts.append("harvested draft" if (meta.get("draft") or meta.get("bundle_id")) else "curated entry")
        if not meta.get("statement_fidelity_reviewed"):
            parts.append("statement not reviewed against its source")
        if read:
            verdict = "no gap found" if read.get("passed") else "a gap was found"
            who = "a human read" if read.get("human") else "an agent read"
            parts.append("%s on %s by %s: %s" % (who, read.get("when") or "?", read.get("who") or "?", verdict))
            parts.append("provisional" if (read.get("human") and read.get("passed")) else "unverified")
        else:
            parts.append("unverified")
        return "; ".join(parts)
    except Exception:
        return "unverified"


def _is_campaign(node):
    """A private research campaign's working deposits, by their own provenance.

    Read from metadata rather than matched against titles: three successive title
    patterns each missed a tier of deposits that carried no campaign word at all.
    """
    meta = _node_meta(node)
    # 2026-09-08: the subject-word match on domain / logical_id / title called 717 nodes a campaign deposit while only
    # 344 live in a research.* namespace - the rest were ordinary curated mathematics about percolation and FPP, so
    # with MAX_CAMPAIGN_SLOTS = 1 a percolation question could be answered with at most one percolation node (the
    # Harris-Kesten p_c = 1/2 node was suppressed on the 09-07 recall bank). Campaign membership is PROVENANCE:
    # a research.* logical id, a campaign source or bundle, or an UN-NAMESPACED working deposit whose domain is the
    # campaign's subject (the FPP lemma tier carries no logical id at all - measured 2026-09-08: 373 such nodes).
    # A curated node with its own logical id (math:probability.percolation:..., harvest.wikipedia:...) is never a
    # campaign deposit just because its subject is percolation.
    lid = str(meta.get("logical_id") or "")
    if re.search(r"(?:^|[:.])research\.", lid):
        return True
    # 2026-09-12 (sandbox measurement, default OFF): the arXiv harvest lanes (math:harvest.arxiv.*) helped the fresh bank
    # at 476 nodes (0.764 -> 0.787 unnamed served) and crowded it at 5,666 (0.742). Under MATHBRAIN_HARVEST_CAP=1 a
    # harvest.arxiv node is capped like a campaign deposit: one slot unless the ranker's own top hit is one.
    if HARVEST_CAP and "harvest.arxiv" in lid:
        return True
    if re.search(r"campaign", str(meta.get("source") or ""), re.IGNORECASE):
        return True
    if not lid and CAMPAIGN_PAT.search(str(meta.get("domain") or "")):
        return True
    return False


#: an encyclopedic opening - "In mathematics, X is ...", "Hoeffding's inequality is ..."
#: - as opposed to a node whose body states the theorem.
LEDE_RE = re.compile(
    r"^(in mathematics|in probability|in the mathematical|in statistics|"
    r"in functional analysis|in measure theory|in combinatorics|in geometry|"
    # 2026-09-03: the harvest opens 184 of its 704 Wikipedia ledes with a field the list above
    # never named ("In topology,", "In number theory,", "In computational complexity theory,",
    # "In the mathematics of evolving systems,"), so the same-title swap left those openings in
    # the block above their statement siblings. An "In <field>, ..." opening whose field names a
    # discipline is a lede; "In any inner product space, ..." is a statement and stays one (the
    # first, unrestricted form flagged 16 authored statements - measured before landing).
    r"in (?:the )?[a-z' -]{0,40}?(?:theory|analysis|algebra|geometry|topology|logic|calculus|"
    r"mathematics|probability|statistics|combinatorics|computability|complexity|physics|"
    r"mechanics|science|optimi[sz]ation|learning)(?: of [a-z' -]{1,40})?, |"
    r"[A-Z][\w' -]{2,40} (is|are|was|refers to) )",
    re.IGNORECASE,
)


def _is_lede(node):
    """True when the body reads like an encyclopedia opening rather than a statement.

    Used ONLY to choose between nodes that share a normalised title, so the subject is
    identical either way and the choice cannot change WHICH theorem is served - only
    whether the block carries its statement or its opening paragraph.
    """
    return bool(LEDE_RE.match(" ".join((node.body or "").split())))


# PRECISION CUE - TRIED AND REJECTED ON MEASUREMENT 2026-09-13 (goal 30h item 3). Kept here, inert at
# MATHBRAIN_PRECISION_BOOST=0, so the next reader gets the numbers instead of re-deriving the idea.
# WHAT IT BOUGHT, at boost 2 on the value-in-block bank (100 keyed items, the instrument built for "did the stored
# value reach the served block"): campaign vib 16 -> 17 and ALL 96 -> 97, served unchanged at 97 - one item gained,
# none lost. WHAT IT COST, the same boost on the fresh bank, matched pair, diffed per item: the unnamed Ramsey item
# went from served and rank-1 to a MISS (unnamed served 72 -> 71, rank-1 54 -> 53, silent 9 -> 10; named, absent
# fires and the 100 long prompts unchanged). It trades a famous-theorem retrieval for a campaign value, so it does
# not land. The diagnosis it came from still stands and is still open: on l1_c10 both cards carrying 1.2579 DO clear
# the prefilter floor (21.9 and 21.7 against 17.5, ranks 5 and 7 of 300), so the drop is in RANKING - but several
# same-campaign siblings also carry decimals, the boost lifts them equally, and the value never reaches the block at
# boost 1, 2 or 4. The finer signal that question needs is the phrase it names ("absolute constant C"), which the
# symbol channel scores at an effective 0.135. Controls for the cue itself: lanes/m7_brain/precision_cue_controls.py
# (22/22). Do not flip this dial without a pair that gains on BOTH banks.
#
# The design, for the record (behind MATHBRAIN_PRECISION_BOOST - default 0).
# A question that asks for a VALUE at a stated precision can only be answered by a card that CARRIES a numeral
# at that precision. Traced on l1_c10 ("the rooted NAE cycle at n = 13 forces the absolute constant C to be at
# least what value (to three decimals)?"): the two cards carrying 1.2579 sat at prefilter ranks 6 and 7 while the
# three slots went to same-campaign siblings scoring higher on words alone - and NONE of those siblings contains a
# decimal numeral at all, so no reader could have answered from the block. This is causal: it reads the question's
# own request and the card's own text, never the answer. It is a WEIGHT, not a gate - it reorders the candidates
# retrieval already admitted and can never introduce a card or open a silent prompt.
_ASKS_VALUE = re.compile(
    r"(?:to\s+(?:one|two|three|four|five|six|\d+)\s+decimals?"
    r"|\bdecimal places?\b"
    r"|\b(?:what|which)\s+(?:is|are|was|were)?\s*(?:the\s+)?"
    r"(?:value|number|constant|bound|exponent|ratio|probability|threshold|limit|mean|variance|count)\b"
    r"|\bat\s+least\s+what\b|\bat\s+most\s+what\b"
    r"|\bhow\s+(?:many|much|large|small)\b"
    r"|\b(?:compute|evaluate|give|state)\b[^?\n]{0,40}\b(?:value|constant|number|bound)\b)", re.IGNORECASE)
_WORD_DIGITS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6}
_DEC_RE = re.compile(r"\d+\.(\d+)")


def _asked_precision(prompt):
    """Decimal places the question asks for, or 0 when it asks for a value without naming a precision,
    or None when it is not asking for a value at all."""
    if not _ASKS_VALUE.search(prompt or ""):
        return None
    m = re.search(r"to\s+(one|two|three|four|five|six|\d+)\s+decimals?", prompt or "", re.IGNORECASE)
    if m:
        tok = m.group(1).lower()
        return _WORD_DIGITS.get(tok, int(tok) if tok.isdigit() else 0)
    return 0


def _precision_boost():
    try:
        return float(os.environ.get("MATHBRAIN_PRECISION_BOOST", "0") or 0.0)
    except Exception:
        return 0.0


def _carries_precision(node, places):
    """True when the card's own text holds a numeral at the requested precision (any decimal when places is 0)."""
    text = (getattr(node, "title", "") or "") + " " + (getattr(node, "body", "") or "")
    for frac in _DEC_RE.findall(text[:6000]):
        if len(frac) >= max(places, 1):
            return True
    return False


def _rank_score(hit, precision=None, boost=0.0):
    base = (
        hit.lexical_score
        + SYMBOL_WEIGHT * hit.symbol_score
        + hit.formula_score
        + GRAPH_WEIGHT * hit.graph_score
    )
    if boost and precision is not None and _carries_precision(hit.node, precision):
        base += boost
    return base


def _title_is_the_query(query, title):
    """True when the sub-query names the node itself: same words, order and punctuation aside.

    "Aubin-Lions lemma" against "Aubin-Lions compactness lemma" is a match (the query's words are all there);
    "pointwise identity" against "Pitfall: Poisson Summation Needs Regularity" is not.
    """
    def words(s):
        s = unicodedata.normalize("NFKD", s or "").casefold()
        s = "".join(c for c in s if not unicodedata.combining(c))
        return {w for w in re.findall(r"[a-z][a-z-]{2,}", s) if w not in ("the", "and", "for", "with", "its")}
    q, t = words(query), words(title)
    if len(q) < 2 or not t:
        return False
    return q <= t or (len(q & t) >= 2 and len(q & t) >= len(q) - 1)


# IDENTIFIER CUE - a candidate, dial default OFF (MATHBRAIN_IDENT_BOOST=0), 2026-09-13.
# Measured problem: a question that names a problem the way a mathematician does ("Hasebe Ueda Wang problem
# 4.7", "Aldous Fill open problem 4.30") carries almost no weight in retrieval - the surrounding words decide.
# Change one digit of the number and leave every other word alone and the hook serves the neighbour's result
# card 8 times in 10, at every near-tie threshold including today's. On the REAL owner-voice bank the harm is
# nil (39 near-miss questions built from real neighbouring problems served zero wrong result cards), so this
# cue is not built to demote - it is built to PROMOTE, which is where the measured prize is: on ov04, ov08 and
# ov30 the right card's own title carries the very identifier the question names, the card sits at prefilter
# rank 2, 3 and 2 of 300, and it still does not reach the block.
_IDENT_RE = re.compile(
    '(?:problem|question|ansatz|conjecture|section|exercise)[ ]+([0-9]{1,3}(?:[.][0-9]{1,3})?)|(?<![0-9.])([0-9]{1,2}[.][0-9]{1,2})(?![0-9.])',
    re.IGNORECASE)


def _idents(text):
    """The problem identifiers a piece of text names. A bare decimal counts - 'problem 7.5' and '7.5' are the
    same reference in his voice - but a bare integer does not, because integers are everywhere in mathematics."""
    return {m.group(1) or m.group(2) for m in _IDENT_RE.finditer(text or "")}


def _coverage_ident_exempt(prompt, scores, top=20):
    """Let a question past the coverage abstention when it names a problem identifier that one of its own top
    candidates carries IN ITS TITLE. Behind MATHBRAIN_COVERAGE_IDENT_EXEMPT, default off.

    Same species as the ENUMERATION exemption already in that gate: a question shape the coverage ratio judges
    badly. ov30 is the measured case - best candidate 0.384 of the question's self-score against a 0.45 floor,
    while its target card sits at prefilter rank 2 of 300 with the very number the question names in its title.
    Lowering the dial instead buys that one answer and pays two extra absent-target fires, and the curve is a
    step, so there is no cheaper setting; this is the targeted version of the same purchase.

    The store read costs nothing on a prompt that names no identifier, which is the common case (0 of the 40
    unanswerable probes name one, 17 of the 30 owner-voice questions do): the wanted set is computed first and
    an empty one returns before any query."""
    if str(os.environ.get("MATHBRAIN_COVERAGE_IDENT_EXEMPT", "0")).strip().lower() in ("0", "", "off", "false"):
        return False
    wanted = _idents(prompt)
    if not wanted:
        return False
    try:
        import sqlite3
        ids = [i for i, _s in sorted(scores.items(), key=lambda kv: -kv[1])[:top]]
        if not ids:
            return False
        con = sqlite3.connect("file:%s?mode=ro" % str(STORE_PATH).replace("\\", "/"), uri=True)
        try:
            q = "select title from nodes where id in (%s)" % ",".join("?" * len(ids))
            for (title,) in con.execute(q, ids):
                if wanted & _idents(title or ""):
                    return True
        finally:
            con.close()
    except Exception:
        return False
    return False


def _ident_boost():
    try:
        return float(os.environ.get("MATHBRAIN_IDENT_BOOST", "0") or 0.0)
    except ValueError:
        return 0.0


def _carries_ident(node, wanted):
    """Only the TITLE is read. A body mentions neighbouring problem numbers in passing ('unlike Problem 7.6');
    the title is where a card states which problem it is about, and all 13 result cards do."""
    if not wanted:
        return False
    return bool(wanted & _idents(getattr(node, "title", "") or ""))


def _select(hits, apply_gates=True, cap=3, title_query=None, prompt=None):
    """Re-rank, gate, deduplicate by title, cap campaign deposits, take `cap` (three by default).

    Returns [] when the store should stay silent.
    """
    # the precision cue (see _asked_precision): a question asking for a value at a stated precision
    # prefers cards that carry a numeral at that precision. Zero boost = this is inert.
    _pb = _precision_boost()
    _prec = _asked_precision(prompt if prompt is not None else (title_query or "")) if _pb else None
    _ib = _ident_boost()
    _want = _idents(prompt if prompt is not None else (title_query or "")) if _ib else set()

    def _rs(hit):
        s = _rank_score(hit, _prec, _pb)
        if _ib and _carries_ident(hit.node, _want):
            s += _ib
        return s

    # An evidence node (a recorded read, a certificate, a citation) is ABOUT a claim; it is not
    # mathematics to serve. The store links it to its claim and indexes its summary like any node,
    # so without this it could take a slot on a query that happens to share its words
    # (2026-09-03, the day the evidence tables stopped being empty).
    ranked = sorted(
        (h for h in hits if str(getattr(h.node, "kind", "") or "").lower() != "evidence"),
        key=lambda hit: (-_rs(hit), hit.node.id),
    )
    if not ranked:
        return []
    if apply_gates:
        top = _rs(ranked[0])
        if top < MIN_SUPPORT:
            return []
        # The margin test asks whether there is a CLEAR WINNER among distinct candidates.
        # Two copies of the same theorem are not two candidates, and comparing against a
        # near-duplicate makes the hook abstain on questions it answers perfectly well.
        #
        # Found 2026-08-30 by the health check after a bulk ingest of two thousand nodes:
        # the dominated convergence query went silent because its statement node and its
        # encyclopedia lede scored 10.033 and 9.935, a margin of 0.0979 against a threshold
        # of 0.1. Nothing about that query got worse - the ingest shifted every score through
        # corpus statistics and pushed an existing near-tie over the edge. Adding material
        # made the brain answer FEWER questions, which is the kind of regression only an
        # end-to-end check catches.
        #
        # So the runner-up is the best node whose normalised title DIFFERS from the leader's.
        # If no such node exists the leader is unopposed and the margin test has nothing to
        # say, so it does not fire.
        #
        # Measured 2026-09-05 on the H1 test (60 questions about the store's own 2,000 enumerated
        # self-avoiding-walk counts): 34 of 60 went SILENT here. The leader and its runner-up were
        # the same sentence with different parameters ("... length 10 ... strip 0 <= y < 3 ...
        # avoid the sites (4,2)" against "(-4,2), (4,1)"), scored a hair apart, and the margin
        # test read that as "no clear winner". But two members of one enumerated family are not
        # two competing answers: the block that goes out carries the parameters, and the reader
        # picks the row that matches (the Opus solver did exactly that in the H1 pilot - it
        # abstained when the sites differed and copied when they matched). So the runner-up must
        # differ from the leader in WORDS, not only in digits: titles that agree once every digit
        # run is replaced by a placeholder are one family, and the margin test does not fire on
        # them. The more precisely a family is enumerated, the more often the old rule silenced
        # it - the same "adding material made the brain answer fewer questions" shape as above.
        def _family_key(title):
            return re.sub(r"\d+", "#", re.sub(r"[^a-z0-9]+", " ", (title or "").casefold())).strip()
        # 2026-09-13: a CAMPAIGN is a family too. One research campaign deposits many notes that differ in words,
        # not only in digits, so the digit-placeholder rule above never covered them and the margin test read two
        # notes from the same campaign as two competing answers. Measured on the 09-11 locate bank: of the three
        # campaign questions the brain served nothing for, l1_c12 died exactly here with "three arm NAE star
        # absorption failure" - the right note - on top, and its rival a sibling from the same campaign. Two
        # deposits of one campaign are not rival answers: the block carries both and the reader picks, which is
        # the argument the enumerated families won on 2026-09-05. MATHBRAIN_CAMPAIGN_FAMILY=0 restores the old rule.
        _CAMPAIGN_FAMILY = os.environ.get("MATHBRAIN_CAMPAIGN_FAMILY", "1") != "0"
        def _campaign_key(node):
            if not _is_campaign(node):
                return None
            meta = _node_meta(node)
            lid = str(meta.get("logical_id") or "")
            m = re.search(r"research\.([a-z0-9_-]+)", lid)
            if m:
                return "research." + m.group(1)
            src = str(meta.get("source") or "")
            if re.search(r"campaign", src, re.IGNORECASE):
                return "source:" + re.sub(r"\s+", " ", src.casefold()).strip()[:60]
            return "domain:" + re.sub(r"\s+", " ", str(meta.get("domain") or "").casefold()).strip()[:60]
        _top_campaign = _campaign_key(ranked[0].node) if _CAMPAIGN_FAMILY else None
        top_key = re.sub(r"[^a-z0-9]+", " ", (ranked[0].node.title or "").casefold()).strip()
        top_family = _family_key(ranked[0].node.title)
        rival = next(
            (h for h in ranked[1:]
             if re.sub(r"[^a-z0-9]+", " ", (h.node.title or "").casefold()).strip() != top_key
             and _family_key(h.node.title) != top_family
             and not (_top_campaign is not None and _campaign_key(h.node) == _top_campaign)),
            None,
        )
        if rival is not None and (top - _rs(rival)) < MIN_MARGIN:
            # MEASUREMENT WITHDRAWN 2026-09-13: the numbers below were taken while `unicodedata` was not imported
            # in this module, so _title_is_the_query raised NameError, the exception left _select through the main
            # try, and the hook served NOTHING for those items - the arm measured "silent", not "exempted". The
            # import is fixed; this exemption is now UNMEASURED, not rejected. Re-run both arms before believing
            # either. The diagnosis it rests on stands: 18 of 53 named
            # sub-queries die here while the RIGHT node sits on top (Vitali-Caratheodory top 8.6 against a
            # floor of 2.6, the shadowing lemma 14.7, Aubin-Lions 8.2), because the store answers a name with
            # that node plus its near-scoring siblings and the margin test reads them as "no clear winner".
            # But exempting a sub-query that matches the leader's title measures WORSE on the pivot bank:
            # 55 items / 69 titled pivots against 59 / 74, both at MATHBRAIN_TIME_BUDGET_S=90 on the same
            # store - the freed sub-query cards displace base cards that were themselves carrying a used
            # result. (The first comparison that killed this read 55 against 59 with the two arms at DIFFERENT
            # machine loads, which is not a comparison at all - see the time-budget note below.)
            return []
    chosen = []
    seen_titles = {}   # normalised title -> index in `chosen`
    campaign_taken = 0
    # 2026-09-11: the cap exists to keep a closed campaign cluster off famous-theorem queries. When the query
    # is ABOUT the campaign - the ranker's own top hit is a campaign node - the cap starved the answer: on the
    # locate-the-fact bank the harness-count node ranked second behind its method note and the rank-2/3 slots
    # went to Inclusion-Exclusion and the CLT instead (5 of 20 campaign questions). A campaign-led ranking may
    # fill all three slots from the campaign; a textbook-led ranking keeps the one-slot cap unchanged.
    # a harvest node at the top never opens the campaign-led expansion (under MATHBRAIN_HARVEST_CAP): a harvest edge
    # over the textbook node on a famous-theorem question would otherwise turn the whole block into harvest siblings
    _top_lid = str(_node_meta(ranked[0].node).get("logical_id") or "")
    campaign_cap = cap if (_is_campaign(ranked[0].node) and not (HARVEST_CAP and "harvest.arxiv" in _top_lid)) else MAX_CAMPAIGN_SLOTS
    for hit in ranked[:SLOT_WINDOW]:
        if len(chosen) >= cap:
            break
        node = hit.node
        campaign = _is_campaign(node)
        if campaign and campaign_taken >= campaign_cap:
            continue
        # harvest and authored waves can hold the same theorem under
        # near-identical titles; injecting it twice burns a slot
        title_key = re.sub(r"[^a-z0-9]+", " ", (node.title or "").casefold()).strip()
        if title_key in seen_titles:
            # ... and when they do, the higher-ranked one is often the harvested
            # encyclopedia OPENING while the lower-ranked one actually states the
            # theorem. Keeping the first hit served "In mathematics, X is a result
            # about..." in place of the statement on 23 theorems, measured
            # 2026-08-30 over the whole store: Rao-Blackwell at lede 10.54 against
            # statement 0.37, Cartan-Hadamard 7.90 against 0.36, AM-GM 5.53 against
            # 1.25. Both nodes carry the same normalised title, so swapping cannot
            # change WHICH theorem is injected - only whether its statement or its
            # opening paragraph goes out.
            prev_i = seen_titles[title_key]
            if _is_lede(chosen[prev_i].node) and not _is_lede(node):
                chosen[prev_i] = hit
            continue
        seen_titles[title_key] = len(chosen)
        chosen.append(hit)
        if campaign:
            campaign_taken += 1
    # A statement sibling can sit BELOW the slot window while its opening paragraph
    # sits above it - five of the twenty-three cases above were exactly that, and the
    # swap in the loop could not reach them. So make one pass over the full ranked
    # list for the ledes that survived. This looks past SLOT_WINDOW deliberately and
    # it is safe for the same reason the swap is: a candidate is only considered when
    # it carries the SAME normalised title as a node already chosen, so it cannot
    # introduce a subject the window had excluded - it can only replace an opening
    # with the statement of a theorem that is already going out.
    if len(ranked) > SLOT_WINDOW:
        chosen_keys = {
            re.sub(r"[^a-z0-9]+", " ", (h.node.title or "").casefold()).strip(): i
            for i, h in enumerate(chosen) if _is_lede(h.node)
        }
        if chosen_keys:
            for hit in ranked[SLOT_WINDOW:]:
                if not chosen_keys:
                    break
                key = re.sub(r"[^a-z0-9]+", " ",
                             (hit.node.title or "").casefold()).strip()
                i = chosen_keys.get(key)
                if i is not None and not _is_lede(hit.node):
                    chosen[i] = hit
                    del chosen_keys[key]
    # A campaign log does not outrank real mathematics in the block - UNLESS it clearly
    # outranks it, which the first version of this rule could not express.
    #
    # The unconditional form put a node scoring 9.56 behind a heat-equation maximum
    # principle at 2.94 and a triangle removal lemma at 2.74, on a question about
    # first-passage percolation. Measured 2026-08-30 over the bank queries: on
    # campaign-drawn questions 90 percent of blocks had a better node pushed below the
    # first slot, every one of them by more than three score points and the worst by
    # 64.7; on famous-theorem questions the same figure is 6 percent. So the penalty was
    # nearly harmless where it was designed to help and severe where it was not.
    #
    # The repair is the one this brain's own note asked for in August and did not get: a
    # category penalty that cannot be outvoted by a rank-1 match is a bug. A campaign
    # node keeps its place when it beats the best non-campaign node by DEMOTION_MARGIN;
    # otherwise it is demoted exactly as before. The cap and the ordering are untouched.
    #
    # Margin chosen by sweep, not by taste. At 1.0 the inversion rate goes to zero on
    # BOTH populations and the campaign share of slots on famous-theorem queries is
    # unchanged at 4.6 percent - the crowd-out that justified leaving this alone does not
    # occur, because nothing new is admitted to the block, only reordered within it.
    campaign_hits = [h for h in chosen if _is_campaign(h.node)]
    plain = [h for h in chosen if not _is_campaign(h.node)]
    best_plain = max((_rs(h) for h in plain), default=float("-inf"))
    earned = [h for h in campaign_hits if _rs(h) > best_plain + DEMOTION_MARGIN]
    demoted = [h for h in campaign_hits if h not in earned]
    return earned + plain + demoted


#: a claim node whose title runs this long carries the old projection
LEGACY_CLAIM_TITLE_CHARS = 200
MAX_ASSUMES_CHARS = 220
MAX_LABEL_CHARS = 120


def _render_parts(node, query="", max_text_chars=None):
    """label, text for one hit - reuniting statements with their assumptions.

    `max_text_chars` is accepted for signature parity with the package renderer (which
    honours it); this fallback uses MAX_STATEMENT_CHARS.

    `query` is accepted and IGNORED here on purpose. The package copy
    (math_brain.rendering.render_parts) uses it to pick the passage of an
    over-long statement that matches the question instead of cutting from the
    head, which is worth 9 of 40 items against 27 of 40 on the retrieval-
    dependent banks. This is the fallback for a missing or broken build, and
    duplicating that scorer here is how a fallback silently drifts a version
    behind the thing it stands in for. Missing the build degrades to head
    truncation; it does not break.

    Two claim-node shapes exist in the store. Legacy (appended before
    2026-08-29): the whole statement in the TITLE and only the assumptions in
    the BODY, so rendering body-as-content served a conditional claim without
    its conditions - the transfer-principle rows went out as unconditional.
    New (store.py 437f805): short title, body = statement + 'Assumes:' clause.
    Both shapes render the same way here: label from the statement head, text =
    statement plus a capped Assumes tail that truncation can never drop.
    """
    title = " ".join((node.title or "").split())
    body = " ".join((node.body or "").split())
    is_claim = bool(_node_meta(node).get("claim_kind"))
    if is_claim and len(title) >= LEGACY_CLAIM_TITLE_CHARS:
        # legacy body = "\n".join(assumptions); keep the boundaries visible
        statement = title
        assumes = "; ".join(
            part.strip() for part in (node.body or "").splitlines() if part.strip()
        )
        label = statement[:MAX_LABEL_CHARS]
        if " " in label:
            label = label[: label.rfind(" ")]
        label += "..."
    else:
        label = title
        statement, assumes = body, ""
        if is_claim and " Assumes: " in body:
            statement, assumes = body.rsplit(" Assumes: ", 1)
    text = statement
    if assumes:
        clause = "Assumes: " + assumes
        if len(clause) > MAX_ASSUMES_CHARS:
            clause = clause[: MAX_ASSUMES_CHARS - 3] + "..."
        budget = max(MAX_STATEMENT_CHARS - len(clause) - 4, 80)
        if len(text) > budget:
            text = text[: budget - 3] + "..."
        text = text + " || " + clause
    elif len(text) > MAX_STATEMENT_CHARS:
        text = text[: MAX_STATEMENT_CHARS - 3] + "..."
    return label, text


SESSION_STATE = os.path.join(HOME, ".claude", "dreaming", ".math_sense_session_state.json")
SESSION_STATE_TTL_S = 48 * 3600


def _already_injected(session_id, node_ids, *, record=True):
    """Return the subset of node_ids already injected for this session, and record
    the rest. Repetition guard (2026-09-01): the same loop prompt re-fired one
    identical block four times in a session - pure context waste. Fail-safe: any
    error returns an empty set (inject as before, never suppress on a broken read)."""
    try:
        if not session_id:
            return set()
        now = time.time()
        state = {}
        if os.path.exists(SESSION_STATE):
            try:
                with io.open(SESSION_STATE, encoding="utf-8") as f:
                    state = json.load(f)
            except ValueError:
                if not record:
                    return set()
                # 2026-09-01: two hook processes raced on ONE shared ".tmp" name (both
                # opened "w", the shorter write left the longer one's tail), the file
                # became unparseable, and this except returned set() on every call - the
                # guard was silently OFF for the rest of the day. A bad state file is
                # quarantined (not read as empty and overwritten) and the guard restarts
                # from a clean file rather than staying dead.
                try:
                    os.replace(SESSION_STATE, SESSION_STATE + ".corrupt_%d" % int(now))
                except OSError:
                    pass
                state = {}
        if not isinstance(state, dict):
            state = {}
        state = {
            s: rec
            for s, rec in state.items()
            if isinstance(rec, dict) and now - rec.get("ts", 0) < SESSION_STATE_TTL_S
        }
        rec = state.get(session_id) or {"ids": [], "ts": now}
        seen = set(rec.get("ids") or [])
        dup = set(node_ids) & seen
        if not record:
            return dup
        rec["ids"] = sorted(seen | set(node_ids))[:400]
        rec["ts"] = now
        state[session_id] = rec
        # Per-process temp name: a shared ".tmp" is exactly the race above.
        tmp = "%s.tmp.%d.%d" % (SESSION_STATE, os.getpid(), int(now * 1000) % 1000000)
        with io.open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f)
        try:
            os.replace(tmp, SESSION_STATE)
        except OSError:
            # Lost the replace race (Windows sharing violation): the other writer's
            # state wins this round; drop our temp and never leave it behind.
            try:
                os.remove(tmp)
            except OSError:
                pass
        return dup
    except Exception:
        return set()



def _prefilter_revision_nodes(store, prefilter_ids, max_revision_nodes=4096):
    """Load candidate revisions without letting the lexical cut revive history.

    SUPERSEDES points from newer to older. Its incoming closure must be present
    before retrieve() decides which immutable revision is current. A missing
    endpoint quarantines its connected revision family; it never enables an old
    record as a fallback. This does not transfer lexical scores to new wording.
    """
    from math_brain.models import EdgeKind

    edges = list(store.iter_edges())
    revisions = [e for e in edges if e.kind is EdgeKind.SUPERSEDES]
    newer, connected = {}, {}
    for edge in revisions:
        newer.setdefault(edge.target_id, set()).add(edge.source_id)
        connected.setdefault(edge.source_id, set()).add(edge.target_id)
        connected.setdefault(edge.target_id, set()).add(edge.source_id)
    wanted = set(prefilter_ids)
    frontier = list(wanted)
    expanded = 0
    while frontier:
        old_id = frontier.pop()
        for new_id in newer.get(old_id, ()):
            if new_id not in wanted:
                expanded += 1
                if expanded > max_revision_nodes:
                    raise ValueError("revision closure exceeds its bounded budget")
                wanted.add(new_id)
                frontier.append(new_id)
    loaded = store.get_nodes(sorted(wanted))
    blocked = wanted - set(loaded)
    frontier = list(blocked)
    while frontier:
        absent = frontier.pop()
        for neighbor in connected.get(absent, ()):
            if neighbor in wanted and neighbor not in blocked:
                blocked.add(neighbor)
                frontier.append(neighbor)
    # Original candidates keep their order; deterministic revisions follow.
    order = list(dict.fromkeys(prefilter_ids))
    order.extend(sorted(wanted - set(order)))
    nodes = [loaded[nid] for nid in order if nid in loaded and nid not in blocked]
    keep = {node.id for node in nodes}
    return nodes, [e for e in edges if e.source_id in keep and e.target_id in keep]



def _source_is_excerpt(node, rendered_text):
    """Compare the rendered content with the complete projected source text."""
    title = " ".join((node.title or "").split())
    body = " ".join((node.body or "").split())
    if _node_meta(node).get("claim_kind") and len(title) >= LEGACY_CLAIM_TITLE_CHARS:
        assumes = "; ".join(part.strip() for part in (node.body or "").splitlines() if part.strip())
        full = title + (" || Assumes: " + assumes if assumes else "")
    elif _node_meta(node).get("claim_kind") and " Assumes: " in body:
        statement, assumes = body.rsplit(" Assumes: ", 1)
        full = statement + " || Assumes: " + assumes
    else:
        full = body
    return rendered_text != full


def _assemble_note_block(header, pieces, budget):
    """Fit whole rendered cards, including their hints, and return emitted IDs.

    A rendered card can itself contain an explicitly identified source excerpt.
    Outer budgeting must not remove that disclosure or its trust/assumption hint.
    """
    if budget <= 0 or len(header) > budget:
        return "", []
    chosen, used, omitted = [], len(header), 0
    for nid, piece in pieces:
        if used + 1 + len(piece) <= budget:
            chosen.append((nid, piece))
            used += 1 + len(piece)
        else:
            omitted += 1
    if omitted:
        while True:
            notice = "[math-brain: %d note(s) omitted whole for the context budget.]" % omitted
            if used + 1 + len(notice) <= budget:
                break
            if not chosen:
                return (notice if len(notice) <= budget else ""), []
            _, removed = chosen.pop()
            used -= 1 + len(removed)
            omitted += 1
    else:
        notice = ""
    block = header + "".join("\n" + piece for _, piece in chosen)
    if notice:
        block += "\n" + notice
    return block, [nid for nid, _ in chosen]


# CLAUSE SPLIT (2026-09-12 night). A question that names two parameter sets in two clauses ("N_A avoids (1,2) and
# (3,0), and N_B avoids (-2,1)") is a query with two lexical centres: sibling rows that partially match BOTH sets outscore
# either row that matches one fully, and the three-slot block fills with near-misses. Measured on the L3 two-fact bank
# (30 questions, both rows needed): whole question serves both rows on 4 / one on 15 / none on 11; asking each
# tuple-carrying clause on its own (the prompt minus the other tuple clauses) and merging the blocks round-robin serves
# both on 11 / 15 / 4. Scope is deliberately narrow: two or three clauses that carry numeric tuples (a lone (0,0) does
# not count), prompt under 1,500 characters - one archived prompt in 34,070 qualifies, so this is a block-composition
# rule for multi-fact questions, not a new way to fire. MATHBRAIN_CLAUSE_SPLIT=0 opts out.
CLAUSE_SPLIT = os.environ.get("MATHBRAIN_CLAUSE_SPLIT", "1") != "0"
# default ON since 2026-09-12 03:xx: on the LIVE store (1,565 harvest.arxiv nodes) the cap read unnamed served 0.764 -> 0.775,
# rank-1 0.551 -> 0.584, silent 0.146 -> 0.124, named and absent-target fires unchanged, locate reach unchanged
# (campaign constants served 17 -> 16 of 20). MATHBRAIN_HARVEST_CAP=0 opts out.
HARVEST_CAP = os.environ.get("MATHBRAIN_HARVEST_CAP", "1") == "1"   # see _is_campaign; measured on a sandbox and live 2026-09-12
# PRE-FLOOR (2026-09-12): apply the on-topic floor to the candidate list BEFORE the three slots are chosen, not only
# after. Traced on the locate bank: the ranker's top three all sat under the floor while floor-passing cards sat at
# ranks 4-13 (a rooted-NAE question: three concept notes at prefilter 15-19 against a floor of 19.2, the two notes
# carrying the value at 21.6 ranked 11th and 13th), so the block went out empty; on another the prefilter's favourite
# at 72.8 ranked 4th and lost its slot to two cards the gate then removed. Flag until measured on every bank.
# DEFAULT 3 since 2026-09-12 03:4x (his clock). Measured the same hour on every bank against the mode-0 base (unnamed
# 69/89 served, absent 27/40, campaign value-in-block 12/20): mode 1 -> 72 / 28 / 14 plus a third-slot fill on 24 of 40
# open-problem questions; mode 2 -> 73 / 31 / 17 (the empty-block rescue fires where the abstention lives); mode 3 ->
# 71 / 27 / 15, named path and reach unchanged, health 11/6. Modes 1 and 2 stay available for his decision.
PREFLOOR_MODE = int(os.environ.get("MATHBRAIN_PREFLOOR", "3") or 0)
PREFLOOR = PREFLOOR_MODE == 1          # mode 1: the whole candidate list is floored before the three slots are chosen
# mode 2 (RESCUE): the block is chosen as before; then (a) an EMPTY gated block is re-chosen among the floor-passing
# candidates, and (b) the prefilter's favourite, when it passes the floor and is not in the block, takes a free slot.
# Mode 1 measured 2026-09-12 03:0x: campaign served 16 -> 18 of 20 and value-in-block 12 -> 14, but it filled a third
# slot with an off-target hub on 24 of the 40 open-problem questions (cards 87 -> 111); mode 2 is the same gain
# without the fill - only an empty block or a missing lexical favourite changes anything.
_TUPLE_RE = re.compile(r"\(\s*-?\d+\s*,\s*-?\d+\s*\)")
_CLAUSE_RE = re.compile(r"(?<=[.;?!])\s+|,\s+and\s+|;\s+")


def _clause_subqueries(prompt):
    if len(prompt) > 1500:
        return []
    parts = [p for p in _CLAUSE_RE.split(prompt) if p and p.strip()]
    tup = [i for i, p in enumerate(parts)
           if [t for t in _TUPLE_RE.findall(p) if t.replace(" ", "") != "(0,0)"]]
    if len(tup) == 1:
        # ... unless ONE clause carries two named parameter sets: "Let S = {(2,1), (3,0)} and T = {(2,1)}". _CLAUSE_RE
        # cuts on ", and" but not on a bare " and ", so both sets stayed in one clause, the split bailed, and the whole
        # question went to the ranker as one long query. Measured 2026-09-13: 5 of the 1,000-item test's items - all
        # saw_growthdiff - were the only ones the brain served NOTHING for, with three right-family cards selected and
        # a whole-prompt score of 23-29 against the on-topic floor of 25. Split that clause on " and " when each half
        # assigns its own set, and each half earns its own sub-query.
        _i = tup[0]
        _halves = [h for h in re.split(r"\s+and\s+(?=[A-Za-z]\w*\s*=\s*\{)", parts[_i]) if h.strip()]
        if len(_halves) >= 2 and all(_TUPLE_RE.search(h) for h in _halves):
            parts = parts[:_i] + _halves + parts[_i + 1:]
            tup = [j for j, p in enumerate(parts)
                   if [t for t in _TUPLE_RE.findall(p) if t.replace(" ", "") != "(0,0)"]]
    if not 2 <= len(tup) <= 3:
        return []
    # the clauses AFTER the last tuple clause ("What is N_A - N_B? Give the exact ...") name both quantities and pull
    # the sub-query back toward the whole question; dropping them read 11 -> 13 of 30 both-served offline
    last = max(tup)
    return [" ".join(p for j, p in enumerate(parts) if j == i or (j not in tup and j < last)) for i in tup]


# A result the argument NAMES: a run of capitalised words (hyphens and accents allowed) in front of the word that
# says what kind of thing it is. "Vitali-Caratheodory theorem", "Lax-Milgram theorem", "Rellich-Kondrachov
# compactness", "Holder's inequality", and the "theorem of X" form.
_KIND_WORD = (r"theorem|lemma|inequality|principle|criterion|identity|formula|decomposition|representation|"
              r"compactness|embedding|convergence|rule|estimate|construction")
# WIDE form (MATHBRAIN_SUBQ_WIDE=1, default OFF): accepts the curly apostrophe ("Egorov’s theorem" as an author
# actually types it), a lowercase particle inside the name ("von Neumann mean ergodic theorem"), and a short
# lowercase tail. It finds strictly MORE real names - 45 pct of the pivots a prompt names, against far fewer -
# and it still measures WORSE at the default three cards, because every extra sub-query spends a card slot that
# an item-specific card was using: 57 items / 79-83 results against 60 / 85, in every sub-query budget from 1 to
# 4 and in both prompt and proper-name order. It PAYS only when the block is widened with it:
#   5 cards + 6,000 chars, wide extractor:  60 items / 96 results   (against 60 / 85 at the default)
# So the scarce resource is the slot, not the query. Default narrow; switch both dials together or neither.
_NAMED_RESULT_RE_WIDE = re.compile(
    r"(?:\b((?:(?:von|van|de|del|della|der|den|du|la|le|ten|ter|of)\s+)?"
    r"[A-Z][\w'’À-ſ]+(?:[-‐-―][A-Z][\w'’À-ſ]+)*"
    r"(?:\s+(?:(?:von|van|de|del|della|der|den|du|la|le|ten|ter)\s+)?[A-Z][\w'’À-ſ]+){0,2}"
    r"(?:\s+[a-z][a-z-]{2,}){0,2})\s*['’]?s?\s+(" + _KIND_WORD + r")\b)"
    r"|(?:\b(" + _KIND_WORD + r")\s+of\s+([A-Z][\w'’À-ſ]+(?:[-‐-―][A-Z][\w'’À-ſ]+)*)\b)"
    r"|(?:\b((?:[a-z][a-z-]{2,}\s+){1,3})(" + _KIND_WORD + r")\b)"
)
_NAMED_RESULT_RE = re.compile(
    # a proper name in front of the kind word: "Lax-Milgram theorem", "Holder's inequality"
    r"(?:\b([A-Z][\w'’À-ſ]+(?:[-‐-―][A-Z][\w'’À-ſ]+)*"
    r"(?:\s+[A-Z][\w'’À-ſ]+){0,2})\s*'?s?\s+(" + _KIND_WORD + r")\b)"
    # "theorem of X"
    r"|(?:\b(" + _KIND_WORD + r")\s+of\s+([A-Z][\w'’À-ſ]+(?:[-‐-―][A-Z][\w'’À-ſ]+)*)\b)"
    # a DESCRIPTIVE name in running prose, which is how most results are actually cited mid-argument:
    # "dominated convergence theorem", "the monotone convergence theorem", "weak maximum principle".
    # One to three lowercase words, none of them a filler word, in front of the kind word.
    r"|(?:\b((?:[a-z][a-z-]{2,}\s+){1,3})(" + _KIND_WORD + r")\b)"
)
# Filler that must never become the name of a result: "the following theorem", "this lemma", "our estimate".
_NAMED_FILLER = {"the", "this", "that", "these", "those", "a", "an", "our", "its", "his", "her", "their",
                 "following", "above", "below", "next", "last", "first", "second", "third", "same", "such",
                 "any", "each", "every", "some", "one", "two", "another", "previous", "preceding", "said",
                 "stated", "standard", "classical", "usual", "well-known", "known", "general", "main",
                 "key", "basic", "simple", "obvious", "trivial", "elementary", "similar", "corresponding",
                 "and", "or", "but", "with", "without", "by", "from", "for", "the-same", "apply", "applying",
                 "using", "use", "used", "invoke", "invoking", "cite", "citing", "see", "recall", "note",
                 "hence", "thus", "so", "since", "because", "then", "now", "here", "where", "which", "whose",
                 "step", "claim", "argument", "proof", "part", "case", "above-mentioned", "aforementioned",
                 # verbs that lead into a result word and would otherwise become its "name"
                 "has", "have", "had", "gives", "give", "given", "giving", "yields", "yield", "implies",
                 "imply", "shows", "show", "states", "state", "holds", "hold", "follows", "follow",
                 "satisfies", "satisfy", "produces", "produce", "provides", "provide", "becomes", "become",
                 "makes", "make", "equals", "equal", "obtain", "obtains", "obtained", "get", "gets",
                 "is", "are", "be", "being", "been", "was", "were", "does", "do", "did", "can", "may"}
_NAMED_SKIP = {"the", "this", "it", "we", "if", "then", "hence", "so", "thus", "by", "since", "let", "for",
               "and", "but", "define", "fix", "here", "now", "step", "claim", "argument", "suppose", "apply",
               "applying", "adding", "note", "every", "each", "both", "first", "second", "third", "finally"}


def _named_result_subqueries(prompt, limit=None):
    """The results a long argument NAMES, as their own queries (2026-09-12 15:4x).

    A proof audit hands the hook two thousand words; the prefilter scores all of them, so the pivot theorem's name
    is one token among hundreds and its node never reaches the ranked list. Measured on the 77 authored proof-audit
    items: the store HOLDS Holder (4 titles), the open mapping theorem (4), Lax-Milgram (5), Rellich (4), Fekete (3),
    and 16 maximum-principle nodes, yet the served block reached only 30 pct of the results those arguments use.
    Asking for each named result on its own is the query the whole-prompt score cannot express.
    """
    if limit is None:
        # How many named results one argument may ask for. Each sub-query spends a card slot, so this trades
        # against the item-specific cards already chosen; measured 2026-09-13 on bank db640d76f2ff at a budget
        # that never trips (items / named results reached, 77 items, 310 results):
        #   3 cards, limit 4: 57 / 83      3 cards, limit 2: see below      5 cards + 6,000 chars, limit 4: 60 / 96
        # against 60 / 85 for the narrower extractor at 3 cards.
        try:
            limit = int(os.environ.get("MATHBRAIN_SUBQ_LIMIT", "4") or 4)
        except Exception:
            limit = 4
    seen, out = set(), []
    _re = _NAMED_RESULT_RE_WIDE if os.environ.get("MATHBRAIN_SUBQ_WIDE", "0") == "1" else _NAMED_RESULT_RE
    for m in _re.finditer(prompt):
        if m.group(1):
            name, kind = m.group(1), m.group(2)
        elif m.group(4):
            name, kind = m.group(4), m.group(3)
        else:
            name, kind = (m.group(5) or "").strip(), m.group(6)
            # a descriptive name is only a name once the filler words are stripped off the front
            words = [w for w in name.split() if w.lower() not in _NAMED_FILLER]
            name = " ".join(words)
        if not name:
            continue
        head = name.split()[0].lower()
        if head in _NAMED_SKIP or head in _NAMED_FILLER or len(name) < 4:
            continue
        q = ("%s %s" % (name, kind)).strip()
        k = q.lower()
        if k in seen:
            continue
        seen.add(k)
        # A PROPER NAME OUTRANKS A DESCRIPTION (2026-09-13). The cut used to take the first `limit` matches in
        # document order, so on a Navier-Stokes argument "satisfying energy inequality", "energy identity" and
        # "energy inequality" filled the budget while Banach-Alaoglu and Gagliardo-Nirenberg - both named in the
        # prompt, both in the store - were never asked for. Rank by how much of a NAME the match carries: a
        # capitalised or hyphenated proper name first, a bare lowercase description last; ties keep prompt order.
        # MEASURED 2026-09-13, bank db640d76f2ff, 3 cards, budget 600: ranking proper names ahead of descriptions
        # READ WORSE than plain prompt order - 57 items / 83 results against 60 / 85, and 51 / 57 when only the
        # single most name-like result was asked for. The first result an argument names is usually the one it
        # hinges on; a proper name mentioned later is often incidental. So prompt order it is, and the ranking is
        # kept only behind MATHBRAIN_SUBQ_RANK=1 for anyone who wants to re-test it against a wider card budget.
        if os.environ.get("MATHBRAIN_SUBQ_RANK", "0") == "1":
            _proper = 2 if (m.group(1) or m.group(4)) else 0
            _cap = min(sum(1 for w in name.split() if w[:1].isupper()), 2)
            out.append((-(_proper + _cap), len(out), q))
        else:
            out.append((0, len(out), q))
    out.sort()
    return [q for _, _, q in out[:limit]]


_QUOTED_RE = re.compile(r"[\"“”]([^\"“”]{8,160})[\"“”]")


def _quoted_subqueries(prompt, limit=4):
    """Each quoted title in the prompt as its own query (2026-09-13 02:4x).

    A question that names several stored entries by title - 'the problems "A", "B" and "C"' - ranks two of them a
    hair apart, and the near-tie margin test reads that as no clear winner and silences the whole answer: 23 of the
    28 items the brain served nothing for on the 1,000-item test died exactly there, with the right family on top.
    The named-result extractor does not see them (a ledger title is not 'X theorem'); the clause split does not
    either (no tuples). Asking for each quoted title on its own is the query whose winner is not ambiguous.
    """
    seen, out = set(), []
    for m in _QUOTED_RE.finditer(prompt):
        q = " ".join(m.group(1).split())
        if len(q.split()) < 2:
            continue
        k = q.casefold()
        if k in seen:
            continue
        seen.add(k)
        out.append(q)
        if len(out) >= limit:
            break
    return out


TITLE_EXEMPT = os.environ.get("MATHBRAIN_TITLE_EXEMPT", "0") == "1"
_TITLE_STOP = frozenset(("the", "and", "for", "with", "its", "from", "that", "this", "campaign", "theorem",
                         "lemma", "proof", "case", "note", "result", "value", "which", "what", "how", "give",
                         "integer", "unknown", "decimal", "exact", "number", "does", "was", "were", "are"))


def _prompt_names_card(prompt, title):
    """True when the prompt carries two or more distinctive words of this card's title.

    Not a similarity score - a presence test on the title's own vocabulary, so it can only fire when the asker
    has written the words the card is filed under.
    """
    low = unicodedata.normalize("NFKD", (prompt or "")).casefold()
    low = "".join(c for c in low if not unicodedata.combining(c))
    t = unicodedata.normalize("NFKD", (title or "")).casefold()
    t = "".join(c for c in t if not unicodedata.combining(c))
    words = {w for w in re.findall(r"[a-z][a-z0-9-]{3,}", t) if w not in _TITLE_STOP}
    if len(words) < 2:
        return False
    return sum(1 for w in words if re.search(r"\b%s" % re.escape(w[:max(4, len(w) - 2)]), low)) >= 2


def _subquery_block(query):
    """The block one sub-query earns on its own: prefilter -> ranker -> _select -> the on-topic floor. Hits, not ids."""
    import math_prefilter
    from math_brain.retrieval import retrieve
    from math_brain.store import ResearchStore
    ids = math_prefilter.select(STORE_PATH, query, k=PREFILTER_K)
    if not ids:
        return []
    ps = dict(getattr(math_prefilter, "LAST_SCORES", {}) or {})
    qself = float(getattr(math_prefilter, "LAST_QSELF", 0.0) or 0.0)
    floor = min(GATE_FLOOR, GATE_FRAC * qself) if (GATE_FRAC > 0 and qself > 0) else GATE_FLOOR
    with ResearchStore.open_read_only(STORE_PATH) as store:
        store.connection.execute("BEGIN")
        nodes, edges = _prefilter_revision_nodes(store, ids)
    result = retrieve(query, nodes=nodes, edges=edges, limit=CANDIDATE_POOL, min_support_score=0.0, min_margin=0.0)
    hits = result.hits
    if PREFLOOR:
        hits = [h for h in hits if float(ps.get(h.node.id, 0.0)) >= floor]
    hits = _tuple_exact_first(query, hits)
    return [h for h in (_select(hits, title_query=query) or []) if float(ps.get(h.node.id, 0.0)) >= floor]


def _norm_tuples(text):
    """The parameter tuples in a text, normalised ("( -2, 1 )" -> "(-2,1)"), the origin excluded."""
    out = set()
    for m in _TUPLE_RE.finditer(text or ""):
        t = re.sub(r"\s+", "", m.group(0))
        if t != "(0,0)":
            out.add(t)
    return out


def _tuple_exact_first(query, hits):
    """Within a tuple-carrying sub-query, prefer the rows whose tuple SET is the query's.

    2026-09-12: on the two-fact bank the block for "avoid the sites (5,1), (6,0)" served the row for "(5,1)" alone - a
    proper subset with a shorter title - because the digits 6 and 0 weigh nothing in either scorer (17 of 30 pairs missed
    a row this way). The lexical scorers cannot see a set; this can. When some candidate carries every query tuple and
    no other, the block is chosen among those; else among the candidates carrying every query tuple; else unchanged.
    _select re-sorts by rank, so the choice is made by RESTRICTING the candidates, not by reordering them. Only the
    clause-split path calls this; a prompt without parameter tuples never reaches it.
    """
    try:
        qt = _norm_tuples(query)
        if not qt or not hits:
            return hits
        exact, superset = [], []
        for h in hits:
            nt = _norm_tuples((h.node.title or "") + " " + (h.node.body or ""))
            if qt <= nt:
                (exact if nt == qt else superset).append(h)
        return exact or superset or hits
    except Exception:
        return hits


def main():
    started = time.time()
    try:
        data = json.load(sys.stdin)
    except Exception:
        return
    prompt = (data.get("prompt") or "")[:8000]
    if len(prompt) < 12 or not _math_gate(prompt):
        return
    if not os.path.exists(STORE_PATH):
        return
    try:
        sys.path.insert(0, BUILD_SRC)
        from math_brain.retrieval import retrieve
        from math_brain.store import ResearchStore

        # The signal cache IS loaded here, and the 2026-08-30 note that stood in this place
        # ("3.26 s median WITH the cache against 2.49 s WITHOUT") measured a cache that never
        # hit: its builder hashed the title and the body separately while retrieve() hashes
        # `title + " " + body`, so every lookup missed and the hook paid the load with none of
        # the saving. Found 2026-09-03 by asking the loaded cache for the combined text. With
        # the key matched, three interleaved pairs in fresh processes over the 4,856-node
        # store: retrieve 0.30 s with the cache against 1.05 s without, whole call 0.71 s
        # against 1.29 s. The cache is a read-only sidecar beside the store, rebuilt by the
        # integrator after every ingest; a stale entry misses and recomputes, never lies.
        # 2026-09-07: a store may carry a lexical PREFILTER index beside it (<store>.prefilter.pkl, built by
        # math_prefilter.py). When it does, only the top PREFILTER_K candidates by BM25 are loaded and ranked -
        # the full-store path costs 0.4 ms per node and the literature layer (46,763 nodes) would take 18 s
        # against this hook's 2.5 s budget. Without an index the path below is byte-for-byte the old one; any
        # failure in the prefilter falls back to it. The signal cache is skipped on the prefilter path (300
        # nodes index on the fly in under a second; the 97 MB cache costs more to load than it saves).
        prefilter_ids = None
        pinned_ids = []
        try:
            sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
            import math_prefilter
            if os.path.exists(math_prefilter.index_path(STORE_PATH)):
                prefilter_ids = math_prefilter.select(STORE_PATH, prompt, k=PREFILTER_K)
                # a result the prompt CITES by LaTeX label is served ahead of the ranked neighbours (2026-09-07:
                # "(ref lem:hitting_prob)" in a proof step -> that lemma's statement, from the literature layer)
                try:
                    pinned_ids = math_prefilter.pinned(STORE_PATH, prompt)
                except Exception:
                    pinned_ids = []
                if prefilter_ids is not None and pinned_ids:
                    prefilter_ids = pinned_ids + [i for i in prefilter_ids if i not in pinned_ids]
        except Exception:
            prefilter_ids = None
        if prefilter_ids is None:
            try:
                from math_brain.retrieval import load_signal_cache
                load_signal_cache(os.path.join(os.path.dirname(STORE_PATH), "brain_signal_cache.pkl"))
            except Exception:
                pass

        # the package copy is the maintained one; this file keeps a fallback
        global _render_parts
        try:
            from math_brain.rendering import render_parts as _render_parts
        except Exception:
            pass

        # coverage abstention (see COVERAGE_MIN): a pinned citation is served regardless, and an ENUMERATION question
        # (self-avoiding / lattice / enumerate ...) is exempt - the H1 health probe is short AND covers little of itself,
        # and the 09-05 margin rule exists to keep that shape speaking (measured 02:5x on every bank + the 11 speak
        # probes: the exemption keeps them all at 11/11 while absent-target fires still fall 34 -> 30 of 40)
        if prefilter_ids and not pinned_ids and COVERAGE_MIN > 0 and not ENUMERATION_ANCHORS.search(prompt):
            try:
                _ps0 = getattr(math_prefilter, "LAST_SCORES", {}) or {}
                _qs0 = float(getattr(math_prefilter, "LAST_QSELF", 0.0) or 0.0)
                if _ps0 and 0 < _qs0 <= COVERAGE_QSELF_MAX and (max(_ps0.values()) / _qs0) < COVERAGE_MIN:
                    if not _coverage_ident_exempt(prompt, _ps0):
                        return
            except Exception:
                pass
        with ResearchStore.open_read_only(STORE_PATH) as store:
            # Keep node, revision-edge, and review reads in one short snapshot.
            store.connection.execute("BEGIN")
            if prefilter_ids is not None:
                if not prefilter_ids:
                    return
                nodes, edges = _prefilter_revision_nodes(store, prefilter_ids)
            else:
                nodes = list(store.iter_nodes())
                edges = list(store.iter_edges())
            reads = _read_records(store)
        if not nodes:
            return
        _elapsed = time.time() - started
        if _elapsed > TIME_BUDGET_S:
            # The budget was spent before a single card was ranked - only ever true on a loaded box, where the
            # 0.15 s prefilter-and-load stage measured on a quiet machine runs 50x slower. Until 2026-09-12 this
            # returned silently and unlogged, and it is the exit the silence log could not see. Now: log it, and
            # proceed anyway while the overrun is bounded (MATHBRAIN_BUDGET_GRACE, default 1.5 x the budget) -
            # the expensive loading is already done, and ranking plus one integrity read is the cheap tail. Past
            # the grace it still refuses, so the prompt is never held indefinitely.
            try:
                _gf = float(os.environ.get("MATHBRAIN_BUDGET_GRACE", "1.5") or 1.5)
            except Exception:
                _gf = 1.5
            if _elapsed > _gf * TIME_BUDGET_S:
                _silence_note(data, "budget_exhausted_before_ranking:%.1fs" % _elapsed)
                return
            _silence_note(data, "budget_exceeded_before_ranking_proceeding:%.1fs" % _elapsed)
        result = retrieve(
            prompt,
            nodes=nodes,
            edges=edges,
            limit=CANDIDATE_POOL,
            min_support_score=0.0,
            min_margin=0.0,
        )
        if not result.hits:
            return
        _hits_for_select = result.hits
        if PREFLOOR and prefilter_ids is not None and not pinned_ids:
            try:
                _psf = getattr(math_prefilter, "LAST_SCORES", {}) or {}
                _qsf = float(getattr(math_prefilter, "LAST_QSELF", 0.0) or 0.0)
                if _psf:
                    _flr = min(GATE_FLOOR, GATE_FRAC * _qsf) if (GATE_FRAC > 0 and _qsf > 0) else GATE_FLOOR
                    _hits_for_select = [h for h in result.hits if float(_psf.get(h.node.id, 0.0)) >= _flr]
            except Exception:
                _hits_for_select = result.hits
        # ARGUMENT CARD BUDGET (MATHBRAIN_ARG_CARDS, default 3 = unchanged; 2026-09-12 15:2x). A prompt carrying a
        # numbered argument asks about several results at once - the 77 hard proof-audit items name 4.0 results each -
        # while a lookup question asks about one. Measured on those items at the default: the block runs 2.3 cards and
        # ~2.1k of the 3k budget, reaches at least one named result on 71 pct of items (shuffled control 14-19) and
        # 30 pct of all named results. Only argument-shaped prompts widen; every other prompt keeps three.
        try:
            _argc = int(os.environ.get("MATHBRAIN_ARG_CARDS", "3") or 3)
        except Exception:
            _argc = 3
        _cap = _argc if (_argc > 3 and ARGUMENT_SHAPE.search(prompt)) else 3
        chosen = _select(_hits_for_select, cap=_cap, prompt=prompt)
        if pinned_ids:
            pin = [h for h in result.hits if h.node.id in pinned_ids][:2]
            rest = [h for h in (chosen or []) if h.node.id not in pinned_ids]
            chosen = (pin + rest)[:_cap] if pin else chosen
        # 2026-09-07 ON-TOPIC GATE (brain-100x goal): a non-pinned note must score at least max(GATE_FLOOR,
        # GATE_RATIO x top) on the prefilter, else it is not served - a weak best match is an off-topic note, and
        # off-topic notes are what cost the morning brain 6.7 pp against sterile on the proof bank. A Mathlib
        # declaration takes at most one slot and only at 0.6 x top. Pinned citations are exempt.
        if chosen and prefilter_ids is not None:
            try:
                ps = getattr(math_prefilter, "LAST_SCORES", {}) or {}
                if ps:
                    # 2026-09-11 (evening): the ratio is measured against the best prefilter score AMONG THE CHOSEN
                    # cards, not the prefilter's global best. The ranker (retrieve) is the primary judge; when its
                    # top card was not the prefilter's favourite, the old rule let the prefilter overrule it and
                    # dropped the card that carried the answer (locate bank: a wordy 99-token question put a long
                    # survey node at prefilter 72.8, the answer node at 18.4, and 0.45 x 72.8 removed the answer).
                    # The floor still applies to every card; the ratio now removes only cards much weaker than the
                    # best card the ranker itself chose.
                    _chosen_ps = [float(ps.get(h.node.id, 0.0)) for h in chosen if h.node.id not in pinned_ids]
                    top = max(_chosen_ps) if _chosen_ps else max(ps.values())
                    # the floor scales with the question (2026-09-11, see GATE_FRAC): a one-line question cannot
                    # reach a sum a 2,000-character proof reaches by length alone
                    qself = float(getattr(math_prefilter, "LAST_QSELF", 0.0) or 0.0)
                    floor = min(GATE_FLOOR, GATE_FRAC * qself) if (GATE_FRAC > 0 and qself > 0) else GATE_FLOOR
                    keep, n_ml = [], 0
                    for h in chosen:
                        nid = h.node.id
                        if nid in pinned_ids:
                            keep.append(h)
                            continue
                        sc = float(ps.get(nid, 0.0))
                        if sc < max(floor, GATE_RATIO * top):
                            # ... unless THE PROMPT NAMES THIS CARD (MATHBRAIN_TITLE_EXEMPT, default 0 under
                            # measurement 2026-09-13). The floor exists because "a weak best match is an off-topic
                            # note", but a long question naming one narrow note scores low by LENGTH, not by being
                            # off topic: locate item l1_c02 names the "FPP chi=1/3 campaign" and its own campaign
                            # note tops the ranker at 22.8 against a scaled floor of 23.4 - lost by 0.6. When two or
                            # more distinctive words of the card's TITLE appear in the prompt, the match is not weak.
                            # MEASURED 2026-09-13 and left OFF: campaign locate 18 -> 19 of 20, but unnamed served
                            # 72 -> 71 of 89, rank-1 54 -> 49, absent-target fires 27 -> 28 of 40. It does not even
                            # rescue the item it was built for: l1_c02 asks about off-U increments while the card it
                            # needs is titled for the block-increment exponent, so the prompt never carries its words.
                            if not (TITLE_EXEMPT and _prompt_names_card(prompt, h.node.title)):
                                continue
                        if str(_node_meta(h.node).get("claim_kind") or "") == "lean_declaration":
                            if n_ml >= 1 or sc < 0.6 * top:
                                continue
                            n_ml += 1
                        keep.append(h)
                    chosen = keep
            except Exception:
                pass
        # PREFLOOR mode 2, the RESCUE (see PREFLOOR_MODE): nothing above changes; only an empty block or a missing
        # lexical favourite does. (a) The gated block is empty although floor-passing candidates exist: re-choose among
        # them and gate again. (b) The prefilter's favourite passes the floor, is not in the block, and a slot is free:
        # it takes the slot (never an evidence node, a Mathlib declaration, a harvest deposit under the cap, or a title
        # already in the block). Traced 2026-09-12 on the locate bank: an FPP question whose answer node was the
        # lexical favourite at 72.8 lost its slot to two cards the gate then removed; two others went out empty while
        # the notes carrying the value sat above the floor at ranks 4-13.
        # mode 3 is (b) alone and only into a NON-EMPTY block: an empty block stays empty, so the abstention behaviour is
        # untouched by construction (mode 2 measured 2026-09-12 03:1x: absent-target fires 27 -> 31 of 40, the rescue of
        # empty blocks fires where the abstention lives, and the lexical coverage ratio does not separate the two).
        if PREFLOOR_MODE in (2, 3) and prefilter_ids is not None and not pinned_ids:
            try:
                _ps2 = getattr(math_prefilter, "LAST_SCORES", {}) or {}
                _qs2 = float(getattr(math_prefilter, "LAST_QSELF", 0.0) or 0.0)
                if _ps2 and (PREFLOOR_MODE == 2 or chosen):
                    _flr2 = min(GATE_FLOOR, GATE_FRAC * _qs2) if (GATE_FRAC > 0 and _qs2 > 0) else GATE_FLOOR
                    if not chosen and PREFLOOR_MODE == 2:
                        _fh = [h for h in result.hits if float(_ps2.get(h.node.id, 0.0)) >= _flr2]
                        _re = _select(_fh) if _fh else []
                        if _re:
                            _best = max(float(_ps2.get(h.node.id, 0.0)) for h in _re)
                            chosen = [h for h in _re if float(_ps2.get(h.node.id, 0.0)) >= max(_flr2, GATE_RATIO * _best)]
                    _fav = max(_ps2.items(), key=lambda kv: kv[1])[0]
                    if (float(_ps2.get(_fav, 0.0)) >= _flr2 and len(chosen or []) < 3
                            and all(h.node.id != _fav for h in (chosen or []))):
                        _fh = next((h for h in result.hits if h.node.id == _fav), None)
                        if _fh is not None:
                            _fm = _node_meta(_fh.node)
                            _ftitle = re.sub(r"[^a-z0-9]+", " ", (_fh.node.title or "").casefold()).strip()
                            _titles = {re.sub(r"[^a-z0-9]+", " ", (h.node.title or "").casefold()).strip() for h in (chosen or [])}
                            if (str(getattr(_fh.node, "kind", "") or "").lower() != "evidence"
                                    and str(_fm.get("claim_kind") or "") != "lean_declaration"
                                    and not (HARVEST_CAP and "harvest.arxiv" in str(_fm.get("logical_id") or ""))
                                    and _ftitle not in _titles):
                                chosen = list(chosen or []) + [_fh]
            except Exception:
                pass
        # the clause split (see _clause_subqueries): each tuple-carrying clause earns its own block; merged round-robin
        # ahead of the whole-question cards, three slots, pins untouched (a pinned prompt skips the split)
        if CLAUSE_SPLIT and prefilter_ids is not None and not pinned_ids:
            try:
                subs = _clause_subqueries(prompt)
                # On an argument-shaped prompt the clause split is dead (it bails above 1,500 chars), so the named
                # results are the only sub-queries available. ON by default since 2026-09-12 16:1x, measured on the
                # 77 authored proof-audit items: a served card is TITLED with a result the argument actually uses on
                # 34 items against 24 before (44 pct against 31), titled hits 38 against 24 of 311. Every other bank
                # is unmoved to the digit (health 11/6, fresh bank 71 and 84 of 89, absent fires 21/33 strict, locate
                # reach, value-in-block) - the gate is narrow and those banks carry no argument-shaped prompt.
                # MATHBRAIN_NAMED_SUBQ=0 turns it off.
                # MATHBRAIN_NAMED_SUBQ_ANY=1 (default 0, under measurement 2026-09-12 23:xx) extends the named
                # sub-queries to EVERY prompt that names a result, not only argument-shaped ones: a plain question
                # such as "does the Vitali-Caratheodory theorem need regularity" names its pivot just as a proof does.
                # The guard against it is the absent-target bank: a named result the store lacks would pull its
                # nearest neighbour and fire falsely.
                if not subs and os.environ.get("MATHBRAIN_NAMED_SUBQ", "1") == "1" and (
                        ARGUMENT_SHAPE.search(prompt) or os.environ.get("MATHBRAIN_NAMED_SUBQ_ANY", "0") == "1"):
                    subs = _named_result_subqueries(prompt)
                # Quoted titles (MATHBRAIN_QUOTED_SUBQ, ON by default since 2026-09-13 02:4x): a prompt naming stored
                # entries by their quoted titles gets one sub-query per title - see _quoted_subqueries. Measured: 23 of
                # the 28 items the 1,000-item test found mute now serve (56 of 66 titles named); fresh bank unnamed
                # 71 -> 72 of 89, named 84 unchanged; absent-target fires 28 -> 27 of 40 (strict 21 -> 20 of 33);
                # health 11 speak / 6 silent either way. =0 turns it off.
                if not subs and os.environ.get("MATHBRAIN_QUOTED_SUBQ", "1") == "1":
                    subs = _quoted_subqueries(prompt)
                if subs and (time.time() - started) < 0.5 * TIME_BUDGET_S:
                    blocks = [_subquery_block(s) for s in subs]
                    merged, seen = [], set()
                    for depth in range(3):
                        for b in blocks:
                            if len(b) > depth and b[depth].node.id not in seen:
                                merged.append(b[depth])
                                seen.add(b[depth].node.id)
                    for h in (chosen or []):
                        if h.node.id not in seen:
                            merged.append(h)
                            seen.add(h.node.id)
                    chosen = merged[:_cap]
            except Exception:
                pass
        if not chosen:
            return
        # Check only the final selected cards, including pinned citations.
        # Never refill rejected slots or alter ranking/margin thresholds.
        from math_brain.selection_integrity import consistent_hits, SelectionUnavailable
        remaining = TIME_BUDGET_S - (time.time() - started)
        try:
            chosen = consistent_hits(chosen, STORE_PATH,
                                     deadline=time.monotonic() + max(0.0, remaining))
        except SelectionUnavailable as exc:
            # GRACEFUL DEGRADATION (2026-09-12 22:4x). The integrity read RAISES when its clock runs out, and the
            # hook's outer except turned that into SILENCE: under ten max-effort solvers the whole hook slowed
            # ~50x, the budget was spent before this line, the deadline was already in the past, and the brain
            # served NOTHING - 0.0 cards on 77 argument prompts at a 0.6 s budget, 0.5 cards at 1.0 s, against
            # 2.8 cards and 59 of 77 pivots with time. A brain that goes mute exactly when the machine is busy,
            # with no trace, is not working fully. So: when the failure is the clock and not the store, spend a
            # small bounded grace on the TOP card alone and serve that. Integrity is intact - nothing is served
            # that the read did not pass - and the overrun is capped by MATHBRAIN_INTEGRITY_GRACE_S. Any other
            # SelectionUnavailable (a card changed under us, an incomplete note) still refuses, as before.
            if str(exc) != "integrity_budget_exhausted":
                _silence_note(data, "integrity:%s" % exc)
                raise
            try:
                _grace = float(os.environ.get("MATHBRAIN_INTEGRITY_GRACE_S", "2.0") or 2.0)
            except Exception:
                _grace = 2.0
            _silence_note(data, "integrity_budget_exhausted:grace_top_card")
            chosen = consistent_hits(chosen[:1], STORE_PATH, deadline=time.monotonic() + _grace)
        from property_order import order_selected
        chosen = order_selected(prompt, chosen, pinned_ids)
        if not chosen:
            return
        # KIND PREFERENCE on an argument the reader is checking (MATHBRAIN_KIND_PREF, default 0 = off; 2026-09-12 15:0x).
        # Reordering only - the same cards, never a different set, never a refill. On a prompt that carries a numbered
        # argument, a theorem or lemma is what decides a step; a definition matches the vocabulary and says nothing about
        # whether the step follows. The block budget truncates, so order is what the reader actually gets. Seen on
        # pa_an_3_07 (a Vitali-Caratheodory argument): the two served cards were "Lebesgue Measure" and "Functions of
        # Bounded Variation", both definitions, while the pivot theorem sat lower or absent.
        if os.environ.get("MATHBRAIN_KIND_PREF", "0") == "1" and len(chosen) > 1 and ARGUMENT_SHAPE.search(prompt):
            try:
                _pins = set(pinned_ids or ())
                _rank = {"theorem": 0, "lemma": 0, "proposition": 0, "corollary": 1, "claim": 1}
                def _kind_rank(h):
                    if h.node.id in _pins:
                        return -1
                    m = _node_meta(h.node)
                    k = str(m.get("claim_kind") or m.get("kind") or h.node.kind or "").lower()
                    return _rank.get(k, 2)
                chosen = [h for _, _, h in sorted(
                    ((_kind_rank(h), i, h) for i, h in enumerate(chosen)), key=lambda t: (t[0], t[1]))]
            except Exception:
                pass
        # CARD-COVERAGE gate (MATHBRAIN_CARDCOV_MIN, default 0 = off; 2026-09-12 08:3x). The block goes out only when the
        # first card carries at least this share of the prompt's rare-word (idf) weight. Measured on the fresh bank's
        # served first cards: AUC 0.913 for true hits (154) against absent-target fires (23); at 0.35 it silences 11 of
        # the 23 fires and 4 of 10 wrong serves and loses 9 of 154 true hits (all unnamed questions). A trade, not a free
        # cut - left OFF for the owner's decision. Pinned citations and enumeration questions are exempt.
        try:
            _ccm = float(os.environ.get("MATHBRAIN_CARDCOV_MIN", "0") or 0)
        except Exception:
            _ccm = 0.0
        # Argument-shaped prompts are exempt too (2026-09-12 23:xx): a 2,000-word proof's idf coverage by ONE card is
        # tiny by construction, so the gate silences the proof audits it should help - measured at CARDCOV_MIN=0.2,
        # pivot reach 59 -> 13 of 77 items and 2.8 -> 0.6 cards, while absent-target fires did not move (28/40 both
        # ways). The gate is a lookup-question instrument; on lookups 0.3 cuts absent fires 28 -> 22 of 40 for 7 of 89
        # unnamed theorems - still a trade for the owner, but no longer one that breaks proofs.
        if (_ccm > 0 and prefilter_ids is not None and not pinned_ids and not ENUMERATION_ANCHORS.search(prompt)
                and not ARGUMENT_SHAPE.search(prompt)):
            try:
                import math as _m
                _d = math_prefilter.load(STORE_PATH)
                _N, _post = _d["n"], _d["post"]
                _qt = {t for t in math_prefilter.tokens(prompt) if t in _post}
                if _qt:
                    def _idf(t):
                        return _m.log(1.0 + (_N - len(_post[t]) + 0.5) / (len(_post[t]) + 0.5))
                    _top = chosen[0].node
                    _ct = set(math_prefilter.tokens((_top.title or "") + " " + (_top.body or "")))
                    _w = sum(_idf(t) for t in _qt)
                    if _w > 0 and sum(_idf(t) for t in _qt & _ct) / _w < _ccm:
                        return
            except Exception:
                pass
        sess_for_guard = data.get("session_id") or ""
        dup_ids = _already_injected(sess_for_guard, [h.node.id for h in chosen], record=False)
        chosen = [h for h in chosen if h.node.id not in dup_ids]
        if not chosen:
            return
        pieces = []
        for hit in chosen:
            node = hit.node
            meta = _node_meta(node)
            kind = str(meta.get("claim_kind") or meta.get("kind") or node.kind)
            label, text = _render_parts(node, query=prompt, max_text_chars=MAX_STATEMENT_CHARS)
            hint = _trust_tag(meta, reads.get(node.id))
            if _source_is_excerpt(node, text):
                hint = "SOURCE EXCERPT: incomplete statement; read the full source and assumptions before applying; " + hint
            pieces.append((node.id,
                "- %s [%s, score %.1f]: %s\n    (source-id: %s)\n    (hint: %s)"
                % (label, kind, _rank_score(hit), text, node.id, hint)
            ))
        # Owner order 2026-09-02 ("a note can mean something good or bad, not necessarily good ... the
        # brain can give a hint"): the block frames every note as a lead that can cut either way. The
        # B12 bank measured why this matters - a served statement about the cited result made a wrong
        # citation or an over-extended scope look legitimate (citation/scope/sharpness faults lost ground
        # under notes while dropped-hypothesis faults gained).
        header = (
            "[math-brain] the math brain holds material that may bear on this - a hint, not a verdict. "
            "A matched result can support the argument or expose it: a note is not evidence that the "
            "argument uses the result correctly, so check the result's hypotheses against the step that "
            "uses it before leaning on it. Nothing below is independently verified. "
            "Full current note: python ~/.claude/dreaming/math_note.py SOURCE_ID"
        )
        # Whole rendered cards only. Source excerpts remain explicitly labeled.
        block, emitted_ids = _assemble_note_block(header, pieces, MAX_BLOCK_CHARS)
        if not block:
            return
        sys.stdout.write(
            json.dumps(
                {
                    "hookSpecificOutput": {
                        "hookEventName": "UserPromptSubmit",
                        "additionalContext": block,
                    }
                }
            )
        )
        sys.stdout.flush()
        if emitted_ids:
            _already_injected(sess_for_guard, emitted_ids)
        sess = data.get("session_id") or ""
        if emitted_ids and re.match(r"^[0-9a-f]{8}-", sess):
            try:
                with io.open(
                    os.path.join(HOME, ".claude", "dreaming", "surfaced.tsv"),
                    "a",
                    encoding="utf-8",
                ) as handle:
                    handle.write(
                        "%.0f\t%s\t%s\tmathbrain\n"
                        % (time.time(), sess[:12], emitted_ids[0][:40])
                    )
            except Exception:
                pass
    except Exception as exc:
        # A swallowed exception here is the brain going silent with no trace. Record the class and reason so
        # silence under load is COUNTABLE (2026-09-12): mathbrain_silence.tsv beside surfaced.tsv.
        try:
            _silence_note(data, "%s:%s" % (type(exc).__name__, str(exc)[:80]))
        except Exception:
            pass
        if os.environ.get("MATHBRAIN_DEBUG"):
            raise
        return


def _silence_note(data, reason):
    """One line per silent or degraded exit, so 'how often does the brain go mute under load' has an answer.

    Format: epoch  session-prefix  reason. Never raises; never blocks the prompt."""
    try:
        sess = str((data or {}).get("session_id") or "")[:12]
        with io.open(os.path.join(HOME, ".claude", "dreaming", "mathbrain_silence.tsv"), "a", encoding="utf-8") as h:
            h.write("%.0f\t%s\t%s\n" % (time.time(), sess, reason))
    except Exception:
        pass


def cli_query(query):
    """Runtime-agnostic recall: print the plain injection block for a query.

    Usage from ANY agent runtime (codex, scripts, humans):
        python math_sense.py --query "your mathematical question"
    Prints nothing when the store abstains. Bypasses the two-anchor prompt
    gate (an explicit query IS intent) but keeps the abstention thresholds.
    """
    try:
        sys.path.insert(0, BUILD_SRC)
        from math_brain.retrieval import retrieve
        from math_brain.store import ResearchStore

        global _render_parts
        try:
            from math_brain.rendering import render_parts as _render_parts
        except Exception:
            pass

        if not os.path.exists(STORE_PATH):
            return
        try:
            from math_brain.retrieval import load_signal_cache
            load_signal_cache(os.path.join(os.path.dirname(STORE_PATH), "brain_signal_cache.pkl"))
        except Exception:
            pass
        with ResearchStore.open_read_only(STORE_PATH) as store:
            # Keep node, revision-edge, and review reads in one short snapshot.
            store.connection.execute("BEGIN")
            nodes = list(store.iter_nodes())
            edges = list(store.iter_edges())
            reads = _read_records(store)
        result = retrieve(
            query, nodes=nodes, edges=edges, limit=CANDIDATE_POOL,
            min_support_score=0.0, min_margin=0.0,
        )
        if not result.hits:
            return
        from math_brain.selection_integrity import consistent_hits
        chosen = consistent_hits(_select(result.hits), STORE_PATH,
                                 deadline=time.monotonic() + TIME_BUDGET_S)
        if not chosen:
            return
        print("Full current note: python ~/.claude/dreaming/math_note.py SOURCE_ID")
        for hit in chosen:
            node = hit.node
            meta = _node_meta(node)
            kind = str(meta.get("claim_kind") or node.kind)
            label, text = _render_parts(node, query=query, max_text_chars=MAX_STATEMENT_CHARS)
            print("[math-brain] %s [%s]: %s" % (label, kind, text))
            print("  (source-id: %s)" % node.id)
            hint = _trust_tag(meta, reads.get(node.id))
            if _source_is_excerpt(node, text):
                hint = "SOURCE EXCERPT: read the full source and assumptions before applying; " + hint
            print("  (hint: %s)" % hint)
            proof = str(meta.get("proof_text") or "").strip()
            if proof:
                print("  proof sketch: %s" % (proof[:400] + ("..." if len(proof) > 400 else "")))
    except Exception:
        return


if __name__ == "__main__":
    if any(a in ("-h", "--help") for a in sys.argv[1:]):
        # help is inert: no stdin read, no store open, no state file touched
        print("usage: math_sense.py                (hook: reads the UserPromptSubmit JSON on stdin)\n"
              "       math_sense.py --query TEXT   (print the note block for TEXT, or nothing)")
        raise SystemExit(0)
    if len(sys.argv) >= 3 and sys.argv[1] == "--query":
        cli_query(" ".join(sys.argv[2:]))
        raise SystemExit(0)
    try:
        _dir = os.path.dirname(os.path.abspath(__file__))
        sys.path.insert(0, _dir)
        import brain_switch as _bsw

        _bsw.gate()
    except SystemExit:
        raise
    except Exception:
        pass
    main()
