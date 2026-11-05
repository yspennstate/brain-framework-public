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
import hashlib
import io
import json
import os
import random
import re
import sys
import time
import unicodedata   # 2026-09-13: two helpers here fold accents; without this they raised NameError into a
# swallowed except, which silently skipped the WHOLE on-topic gate - a behaviour change that reads as a result.

HOME = os.path.expanduser("~")
# Framework deployment: the mathbrain package is vendored beside this hook, and the
# store location is deployment-specific - point MATHBRAIN_STORE at your population db.
_HERE = os.path.dirname(os.path.abspath(__file__))
BUILD_SRC = os.environ.get(
    "MATHBRAIN_SRC", os.path.join(_HERE, "..", "mathbrain", "src")
)
STORE_PATH = os.environ.get("MATHBRAIN_STORE", "")
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
DERIVED_ANCHORS = os.environ.get("MATHBRAIN_DERIVED_ANCHORS", "0").strip() in ("1", "on", "true")
_DERIVED_VOCAB = None


def _derived_anchor_vocab():
    """The corpus-derived anchor words, loaded once. Empty set when the file is missing or unreadable, which
    makes the dial inert rather than erroring - a cost filter must never be the reason a prompt crashes."""
    global _DERIVED_VOCAB
    if _DERIVED_VOCAB is None:
        try:
            with io.open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "math_anchor_vocab.json"),
                         encoding="utf-8") as fh:
                _DERIVED_VOCAB = frozenset(json.load(fh).get("terms") or ())
        except Exception:
            _DERIVED_VOCAB = frozenset()
    return _DERIVED_VOCAB
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


# PROSE GATE (2026-09-14, staged OFF behind MATHBRAIN_PROSE_GATE=1, default off).
# Re-derived as a PATCH from the candidate the 09-09 retrieval diagnosis left pending review
# (audits/evidence/2026/09/09/math-retrieval-diagnosis). That candidate is a whole-file fork of the
# hook as it stood at 982 lines; this file is now 2,489 across 45 commits, so deploying its FILE
# would revert the identifier exemption, the per-session repetition guard, the name-token coverage
# rule and the definition-below-result rule. The idea survives, the artifact does not.
# WHAT IT IS FOR: the diagnosis found 11 real mathematical questions that never reach retrieval
# because their phrasing carries no recognised anchor combination - martingale06/08/11/12 and
# strictness03/07/08/09/10/11/12. The gate is a cost filter, and it was refusing mathematics
# silently. [[the_math_gate_refuses_real_mathematics_and_never_says_so_2026_09_14]]
# WHY IT IS OFF: a widening measured EARLIER TODAY (the derived-anchor list) looked good in
# aggregate - silent 9 -> 5 - while the per-question diff showed one gain against three confident
# wrong cards. Admission is not the outcome; the transition matrix is. This dial stays off until it
# is measured the same way, per question, on both the answerable and the unanswerable arms.
PROSE_GATE = os.environ.get("MATHBRAIN_PROSE_GATE", "0").strip() not in ("0", "off", "false", "")
TECHNICAL_PROSE_FAMILIES = (
    re.compile(r"\bgaussian\b", re.IGNORECASE),
    re.compile(r"\bnormal law\b", re.IGNORECASE),
    re.compile(r"\bcharacteristic functions?\b", re.IGNORECASE),
    re.compile(r"\bconditional mean\b", re.IGNORECASE),
    re.compile(r"\blog[- ]convex(?:ity)?\b", re.IGNORECASE),
    re.compile(r"\bmonotonicity\b", re.IGNORECASE),
)
TECHNICAL_PROSE_REASONING = re.compile(
    r"\b(prove|derive|compute|calculate|construct|determine|establish|show|"
    r"factor\w*|multipl\w*|imply|implies|satisf\w*|converg\w*|convex\w*|"
    r"monotonic\w*|independen\w*|insufficient|equal\w*)\b", re.IGNORECASE,
)
TECHNICAL_PROSE_ACTION = re.compile(
    r"\b(rename|copy|move|delete|remove|open|download|upload|render|export|"
    r"resize|recolor|format|retitle|enlarge|translate|spellcheck|pronounc\w*|"
    r"capitaliz\w*|summarize|find|locate|bigger|smaller|larger|bold|italic)\b",
    re.IGNORECASE,
)
# `paths?` and `strings?` are DELIBERATELY ABSENT from this list, though the 09-09 candidate carried both.
# Measured on that candidate's own corpus: `paths?` matches ZERO of the 60 admin negatives and one real
# mathematical prompt - it buys nothing and costs "the sample paths of the walk", which is core vocabulary in
# his domain. `strings?` is the same shape (a string in a language is mathematics). The nouns that do the work
# are unambiguous: captions 12 admin / 0 maths, movies 8/0, files 7/0, reports 7/0, headings 6/0.
TECHNICAL_PROSE_ARTIFACT = re.compile(
    r"\b(file(?:name)?s?|folders?|directories|reports?|documents?|pdfs?|videos?|movies?|"
    r"clips?|captions?|titles?|fonts?|slides?|labels?|thumbnails?|screenshots?|"
    r"assets?|narration|headings?)\b", re.IGNORECASE,
)
# ADMIN SUPPRESSION ON THE LEGACY PATH (2026-09-14, staged OFF behind MATHBRAIN_ADMIN_SUPPRESS=1).
# The 09-09 candidate scoped its action-plus-artifact suppression to its NEW route only, because the gate of
# that day kept all 60 admin negatives silent. It no longer does: replaying that corpus today, the LEGACY gate
# fires on nine of them - rename/copy/render/export/translate/find the "Gaussian martingale independence"
# movie, PDF, video, captions, title, heading, file. Five days of anchor growth walked the gate into candidate
# v1's exact defect from the other direction, and it lands on his own work, because his films are mathematical.
# MEASURED with this dial on and `paths?` removed: admin negatives firing 9 -> 1 (the survivor, "I like the
# Gaussian martingale movie", carries no action word - a comment, not a request); the 8 development positives
# lose NOTHING; ten real mathematical probes including two "sample paths" sentences lose nothing; and the two
# prompts it removes from the gate-positive set are themselves admin ("make the Cauchy Schwarz theorem title
# larger in the video", "copy the martingale CLT PDF to my Downloads folder") - corrections, not losses.
# It stays off until the regression gate has judged it, because it is the first change tonight that makes the
# gate QUIETER, and a suppression that is wrong is silent about being wrong.
ADMIN_SUPPRESS = os.environ.get("MATHBRAIN_ADMIN_SUPPRESS", "0").strip() not in ("0", "off", "false", "")


def _math_gate(prompt):
    """The legacy gate, then - only when MATHBRAIN_PROSE_GATE=1 - a bounded prose route.

    Every decision the old gate made is preserved: a legacy positive returns True before the new
    code runs, so this can only ADD fires, never remove one. The new route needs a weak technical
    family AND a reasoning cue AND two distinct signals in total, and it suppresses the pair
    (action word + artifact noun) that made candidate v1 admit a report-renaming request.
    """
    if _legacy_math_gate(prompt):
        if (ADMIN_SUPPRESS and TECHNICAL_PROSE_ACTION.search(prompt)
                and TECHNICAL_PROSE_ARTIFACT.search(prompt)):
            return False
        return True
    if not PROSE_GATE:
        return False
    extra = sum(bool(pattern.search(prompt)) for pattern in TECHNICAL_PROSE_FAMILIES)
    if not extra or not TECHNICAL_PROSE_REASONING.search(prompt):
        return False
    if TECHNICAL_PROSE_ACTION.search(prompt) and TECHNICAL_PROSE_ARTIFACT.search(prompt):
        return False
    existing = set(m.group(0).lower() for m in MATH_ANCHORS.finditer(prompt))
    existing |= set(m.group(0).lower() for m in EXTRA_ANCHORS.finditer(prompt))
    existing |= set(m.group(0).lower() for m in GREEK_ANCHORS.finditer(prompt))
    return len(existing) + extra >= 2


def _legacy_math_gate(prompt):
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
    # DERIVED ANCHORS (MATHBRAIN_DERIVED_ANCHORS, default 0 = OFF; 2026-09-14). The anchor lists above are
    # hand-written, so a question can be plainly mathematical and carry none of their words: measured over
    # the 178-question answerable bank, this gate refuses "Which finite graphs are planar, in terms of
    # forbidden subdivisions?", "Asymptotics of the number of primes up to x.", Jensen's inequality written
    # in symbols, and Menger's theorem stated in words - all four of which the store holds cards for.
    # `math_anchor_vocab.json` is a 301-word list DERIVED from the corpus and never from the bank: words of
    # four or more letters in at least 8 of the store's own node titles and in at most 0.2 pct of the
    # memory documents (the bm25 stopword list subtracted by hand, because the negative corpus strips
    # stopwords and therefore cannot see them - the first cut of this list "proved" that "that", "there"
    # and "with" were mathematical vocabulary).
    # MEASURED, AND REFUSED - keep it off. One such word admits 4 of the 5 refused answerable questions and
    # ZERO of the 40 absent-target ones, and the AGGREGATE on the hook-path bank looks like a clear win:
    #     q_content served 72/89 -> 73/89, rank-1 53 -> 54, SILENT 9 -> 5, false fire 27/40 unchanged.
    # The per-question diff says the opposite. Of the four newly admitted, ONE becomes a correct answer
    # (jensen, served "Jensen's Inequality" first) and THREE become confident wrong cards: Menger answered
    # with "Ore's Theorem on Hamiltonian Cycles", Kuratowski with "Robertson-Seymour", the prime number
    # theorem with "Infinitude of Primes". Those are the dangerous kind of wrong - a plausible neighbour
    # that reads as the answer. Trading four silences for one answer and three of those runs straight
    # against his 2026-09-02 order to make the brain fire LESS and be surer when it fires, so the silence
    # is the better behaviour and this stays off.
    # THE COST IN PRODUCTION, separately: over the 33,970 real prompts in the archive, 43.1 pct pass this
    # gate today and 912 more would - a 6 pct rise in admissions, some plainly not questions ("add that
    # code can be found here github..." carries "hamiltonian" from a paper title).
    # If someone revisits this: the aggregate is what fooled me for ten minutes. Diff the questions.
    if DERIVED_ANCHORS and _derived_anchor_vocab() and any(
            w in _derived_anchor_vocab() for w in re.findall(r"[a-z]{4,}", prompt.lower())):
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
    second, independent route.

    TERSE SINCE 2026-09-13, and deliberately back to his own words. The owner's 09-02 order describes
    this line as saying "what kind of thing it is (theorem with a proof text, concept lead, harvested
    draft, unreviewed)" - a kind-tag. It had grown into a caution paragraph averaging 160 characters a
    card, and the caution it carried ("read the full source and assumptions before applying") already
    stands in the block header, once, where it is read once. Measured on the 1,000 captured blocks of
    the 09-12 A/B: hints were 14.2 pct of every block against 49.2 pct for the mathematics itself. The
    meanings are all still here - kind, proof on file or not, draft or curated, statement reviewed or
    not, verified or not, and any recorded read - in about a third of the characters."""
    try:
        ck = str(meta.get("claim_kind") or meta.get("kind") or "").lower()
        parts = []
        if ck in ("theorem", "lemma", "proposition", "corollary"):
            parts.append(ck + (" +proof" if meta.get("proof_text") else ", no proof"))
        elif ck:
            parts.append(ck + " lead")
        parts.append("draft" if (meta.get("draft") or meta.get("bundle_id")) else "curated")
        if not meta.get("statement_fidelity_reviewed"):
            parts.append("unreviewed")
        if read:
            verdict = "no gap" if read.get("passed") else "GAP FOUND"
            who = "human" if read.get("human") else "agent"
            parts.append("%s read %s: %s" % (who, read.get("when") or "?", verdict))
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
# A question that names a problem the way a mathematician does ("Hasebe Ueda Wang problem 4.7") carries almost
# no weight in retrieval - the surrounding words decide. Change one digit and leave every other word alone and
# the hook serves the neighbour's result card 8 times in 10, at every near-tie threshold including today's.
# The card names its own problem in its title, so that is a near miss offered as a lead, not a false claim, and
# on 39 questions about REAL neighbouring problems the hook served zero wrong result cards. So this cue does
# not demote. It PROMOTES: on ov04 and ov08 the right card's title carries the identifier the question names,
# the card sits at prefilter rank 2 and 3 of 300, and it still does not reach the block. Measured on generation
# 4e2fca96: owner-voice own-result-card 20 -> 22 of 30 at boost 2.0, nothing lost, serve count unchanged, zero
# wrong cards on the near-miss probe, the 89-theorem fresh bank byte-identical card for card (0 of its 89
# questions name an identifier, so the cue cannot fire there), timing a wash on interleaved pairs.
# The pattern carries NO backslash on purpose: an escape written through a generator arrived once as the 0x08
# byte and the regex silently matched nothing. Character classes say the same thing and cannot be mangled.
_IDENT_RE = re.compile(
    '(?:problem|question|ansatz|conjecture|section|exercise)[ ]+([0-9]{1,3}(?:[.][0-9]{1,3})?)'
    '|(?<![0-9.])([0-9]{1,2}[.][0-9]{1,2})(?![0-9.])',
    re.IGNORECASE)


def _idents(text):
    """The problem identifiers a piece of text names. A bare decimal counts - "problem 7.5" and "7.5" are the
    same reference in his voice - but a bare integer does not, because integers are everywhere in mathematics,
    and a four-digit decimal does not either, because that is an arXiv number (the negative control caught it)."""
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
    # DEFAULT ON since 2026-09-14 00:4x. Re-measured on the generation it landed on (b18409b9 / patched from
    # 297122a7): inert with both dials unset (0 of 30 blocks differ), exemption alone owner-voice served 25 -> 26,
    # own result card 22 -> 23 of 30, unanswerable fires 27/40 unchanged, adversarial neighbour-card 9/10
    # unchanged; the boost (MATHBRAIN_IDENT_BOOST 2.0) added NOTHING on top on this generation (23/30 either way),
    # so it stays 0 - exactly the "one of the two edits is redundant now" outcome the 09-13 measurement warned of.
    if str(os.environ.get("MATHBRAIN_COVERAGE_IDENT_EXEMPT", "1")).strip().lower() in ("0", "", "off", "false"):
        return False
    wanted = _idents(prompt)
    if not wanted:
        return False
    try:
        import sqlite3
        ids = [i for i, _s in sorted(scores.items(), key=lambda kv: -kv[1])[:top]]
        if not ids:
            return False
        con = sqlite3.connect("file:%s?mode=ro" % str(STORE_PATH).replace(chr(92), "/"), uri=True)
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
    """Only the TITLE is read. A body mentions neighbouring problem numbers in passing ("unlike Problem 7.6");
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
# One shard per session since 2026-09-14; the monolith above is no longer read or written (see
# _session_shard for the measurement that retired it).
SESSION_DIR = os.path.join(HOME, ".claude", "dreaming", ".math_sense_sessions")
SESSION_STATE_TTL_S = 48 * 3600


def _session_shard(session_id):
    """This session's own state file. Sharded 2026-09-14: the guard used ONE json object holding every
    session, and every hook call read, TTL-filtered and RE-SERIALISED all of it to record a few ids for
    one session. Measured that day: 5.2 MB, 19,846 sessions, and `_already_injected` was 0.68 s of a
    1.69 s math-hook call - 0.58 s of it a single json.dump over 300,177 encode calls - a cost that grew
    with every session the fleet had ever run and was paid by every prompt of every agent. One small file
    per session makes it O(1). A session id is hashed, never used as a filename: it is caller-supplied."""
    h = hashlib.sha1((session_id or "").encode("utf-8", "replace")).hexdigest()[:24]
    return os.path.join(SESSION_DIR, h + ".json")


def _prune_session_shards(now, sample=1):
    """Delete shards past the TTL. Runs on roughly one call in `1/sample`, never on the read path's
    critical section, and never raises: a directory scan is cheap but it is not free, and the guard
    must not become the thing it replaced."""
    try:
        if random.random() > sample:
            return
        for n in os.listdir(SESSION_DIR):
            if not n.endswith(".json"):
                continue
            p = os.path.join(SESSION_DIR, n)
            try:
                if now - os.path.getmtime(p) > SESSION_STATE_TTL_S:
                    os.remove(p)
            except OSError:
                pass
    except Exception:
        pass


def _already_injected(session_id, node_ids, *, record=True):
    """Return the subset of node_ids already injected for this session, and record
    the rest. Repetition guard (2026-09-01): the same loop prompt re-fired one
    identical block four times in a session - pure context waste. Fail-safe: any
    error returns an empty set (inject as before, never suppress on a broken read)."""
    try:
        if not session_id:
            return set()
        now = time.time()
        path = _session_shard(session_id)
        rec = {}
        try:
            with io.open(path, encoding="utf-8") as f:
                rec = json.load(f)
        except FileNotFoundError:
            rec = {}
        except ValueError:
            # A half-written shard belongs to this session alone: drop it and start clean rather than
            # leaving the guard dead the way the shared file's corruption did on 2026-09-01.
            if not record:
                return set()
            try:
                os.remove(path)
            except OSError:
                pass
            rec = {}
        if not isinstance(rec, dict) or now - float(rec.get("ts", 0) or 0) >= SESSION_STATE_TTL_S:
            rec = {}
        seen = set(rec.get("ids") or [])
        dup = set(node_ids) & seen
        if not record:
            return dup
        rec = {"ids": sorted(seen | set(node_ids))[:400], "ts": now}
        try:
            os.makedirs(SESSION_DIR, exist_ok=True)
        except OSError:
            pass
        # Per-process temp name: a shared ".tmp" was the 2026-09-01 race.
        tmp = "%s.tmp.%d.%d" % (path, os.getpid(), int(now * 1000) % 1000000)
        with io.open(tmp, "w", encoding="utf-8") as f:
            json.dump(rec, f)
        try:
            os.replace(tmp, path)
        except OSError:
            # Lost the replace race (Windows sharing violation): the other writer's
            # state wins this round; drop our temp and never leave it behind.
            try:
                os.remove(tmp)
            except OSError:
                pass
        _prune_session_shards(now, sample=0.01)
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
    # ANSWER CLOSURE (2026-09-20): a candidate that is the TARGET of a proves/refutes edge brings the edge's source
    # into the pool. The store held six of his results as claims with typed edges to the open-problem nodes and
    # the hook served the question alone, because the prefilter's lexical cut had the question and the ranker
    # never saw the answer. Bounded: at most ANSWER_CLOSURE_MAX sources per candidate, from the whole edge list
    # (already in memory above). Does not transfer scores; the ranker still judges the loaded node.
    try:
        _amax = int(os.environ.get("MATHBRAIN_ANSWER_CLOSURE_MAX", "3"))
    except Exception:
        _amax = 3
    if _amax > 0:
        answers = {}
        for edge in edges:
            if edge.kind is EdgeKind.PROVES or edge.kind is EdgeKind.REFUTES:
                answers.setdefault(edge.target_id, []).append(edge.source_id)
        for qid in list(wanted):
            for src in answers.get(qid, ())[:_amax]:
                if src not in wanted:
                    expanded += 1
                    if expanded > max_revision_nodes:
                        break
                    wanted.add(src)
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



_FLAT_MATH_SYMBOLS = frozenset(u"≤≥∥∑∫√∈")


def _structure_flattened(rendered_text):
    """True when the harvest dropped this statement's fractions, exponents and subscripts.

    Measured 2026-09-15 over the whole store: 560 claims carry real mathematical symbols while
    containing no '/', no '^', no '_' and no 'frac' - 7.1 pct of all claims, and 53.9 pct of the
    harvested-arXiv tier. Eight drawn at random were all genuinely damaged, and the worst of them
    change the claim rather than merely uglifying it: `C_d (1 + LD/eta)^d` is served as
    "Cd 1 + LD eta d", and `N^{R(s1)+R(s2)}` as "N R(s1)+R(s2)", where an exponent has silently
    become a product. An agent reading that card cannot tell an exponent was ever there.

    Deliberately ONE WORD on the hint line, and only on the cards it fires for. The tag was cut to a
    third of its length on 09-13 because hints were displacing mathematics; this does not reopen
    that - a clean card is untouched. The check is cheap and conservative: it misses partial damage
    (a statement keeping one '/' elsewhere passes), so it under-reports rather than over-warns.
    """
    try:
        if not any(ch in _FLAT_MATH_SYMBOLS for ch in (rendered_text or "")):
            return False
        return not any(tok in rendered_text for tok in ("/", "^", "_", "frac"))
    except Exception:
        return False


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


_NAME_STOP = frozenset("""what which how why when where the for in if give state under does do is are let suppose
prove show explain describe a an and or of on to with without that this these those it its be been being can could
should would may might must there here they them their we you your i as at by from into over about than then so
such but not no yes one two both each any all some most more less many much other another same different new old
first second third theorem lemma proposition corollary inequality bound law rule method setting space function
transform process variable measure convergence distribution problem question result statement form version case
example define definition concept pdf""".split())


def _name_tokens(prompt):
    """The prompt's own identity tokens - hyphenated proper names (Tracy-Widom, Hanson-Wright), plain capitalised
    words, acronyms (KKL, LYM) - minus grammar capitals and mathematical furniture. Read from the prompt alone, so
    the signal is causal; the bank's stems are never consulted. Measured 2026-09-13 (memory
    covering_the_questions_names_is_the_best_ranking_signal_measured_2026_09_13). 'pdf' is stopped because the
    locate-bank boilerplate capitalises it on every question."""
    txt = unicodedata.normalize("NFKD", prompt or "")
    txt = "".join(c for c in txt if not unicodedata.combining(c))
    out = set()
    for m in re.finditer(r"\b([A-Z][a-zA-Z]+(?:-[A-Z][a-zA-Z]+)+)\b", txt):
        for part in m.group(1).split("-"):
            if part.lower() not in _NAME_STOP and len(part) > 2:
                out.add(part.lower())
    for m in re.finditer(r"\b([A-Z][a-z]{2,})\b", txt):
        w = m.group(1).lower()
        if w not in _NAME_STOP:
            out.add(w)
    for m in re.finditer(r"\b([A-Z]{2,6})\b", txt):
        w = m.group(1).lower()
        if w not in _NAME_STOP:
            out.add(w)
    return out


DEFN_BELOW_RESULT = os.environ.get("MATHBRAIN_DEFN_BELOW_RESULT", "1").strip() not in ("0", "off", "false")


def _defn_below_result_rerank(chosen):
    """A card the store records as a DEFINITION never sits above one it records as a theorem or lemma.

    The narrow rule out of the 2026-09-13 decisions memo, item 4, which measured it at rank-1 129 -> 131 of 178
    and said plainly that it needed nobody's permission - it is a change to the ranking code. It was never landed.
    Re-measured on the 09-14 gate rows before building it: of the 23 near-misses (the right card served but not
    first) the card that beats it is another THEOREM 9 times, a DEFINITION 7, a concept 5, a lemma 1. So seven is
    the ceiling here and the memo's two is the floor; the nine theorem-beats-theorem cases no kind field can touch.

    MEASURED AND LANDED 2026-09-14, both arms on the hook-path bank, the control arm reproducing the morning's
    clean numbers exactly. (An earlier pair was thrown away: it ran while the box was starving the hook, and
    q_hinted silence had tripled. That - the hook's own baseline - is the validity check, not the governor, which
    read `ok` throughout.)

        q_content rank-1   53/89 -> 57/89          q_hinted rank-1   80/89 -> 81/89
        served 72/89 and 84/89, silent 9 and 3, false fire 27/40 and strict 20/33: ALL UNCHANGED

    Five questions changed and every one is a GAIN, none lost - checked question by question, because class
    totals hid a real loss in a different experiment the same morning. Banach fixed point and Baire category were
    both being beaten by the card "Complete metric space"; the implicit function theorem and Kahn-Kalai-Linial,
    both forms, by their own definitions. The memo estimated +2 against a three-card block; the block carries four
    cards now, and the re-measured near-miss population held seven definition-blocked cases, of which five moved.

    REORDER ONLY, and stable: the same cards go out in the same set, equal-class cards keep the ranker's own
    order, so hit and silence cannot move and only the first card can change. That is deliberate - the memo's own
    predecessor work showed that FILTERING this pool is not neutral (removing candidates moves the margin test's
    leader and the gates computed relative to it, measured 155/310 against 160/310), so a kind signal has to act
    on the order and never on the set. The kind is read from the store's recorded field, which exists on 7,819 of
    7,865 nodes; a card with no recorded kind is treated as a result, never demoted on a missing value."""
    try:
        if not DEFN_BELOW_RESULT or not chosen or len(chosen) < 2:
            return chosen
        def _kind(h):
            m = _node_meta(h.node)
            return str(m.get("claim_kind") or m.get("kind") or getattr(h.node, "kind", "") or "").lower()
        RESULT = ("theorem", "lemma", "proposition", "corollary")
        if not any(_kind(h) in RESULT for h in chosen):
            return chosen                      # nothing to rank a definition below
        return [h for _, _, h in sorted(
            ((1 if _kind(h) == "definition" else 0, i, h) for i, h in enumerate(chosen)),
            key=lambda t: (t[0], t[1]))]
    except Exception:
        return chosen


TITLE_COVER = os.environ.get("MATHBRAIN_TITLE_COVER", "0").strip() in ("1", "on", "true")
_TITLE_COVER_STOP = frozenset("""the for and that with this from under over into what which how why when where
does do is are let suppose prove show state give explain describe about than then them they their its it be
been being can could should would may might must there here we you your as at by of on to in if or a an one two
both each any all some most more less many much other another same different new old first second third such but
not no yes""".split())


def _title_cover_rerank(prompt, chosen):
    """Order the cards a block already carries by how much of the QUESTION each title accounts for.

    The rule above it asks whether a title carries every NAME the question uses, which is the right question when
    the asker names the theorem and inert when they describe it - and describing it is exactly what the hard half
    of the bank does (rank-1 is 89.9 pct on questions that name the result and 59.6 pct on questions that do not).
    This asks the other direction and over ordinary words, not only capitalised ones: of the question's content
    words, what share does this title contain? Tokens match on a five-character prefix, so "converge" reaches
    "convergence" and "planar" reaches "planarity".

    REORDER ONLY, and that is the point: the same cards go out, so a block that was served is still served, a
    silence is still a silence, and no gate that judged the block is disturbed. Only the FIRST card can change,
    which is the only thing rank-1 measures. Stable: equal coverage keeps the ranker's own order.

    The same signal, question-side and smooth rather than thresholded, took the memory brain's deliberate recall
    from hit@1 28 to 32 of 56 the same morning (recall_ranks_by_how_much_of_the_question_the_name_covers).
    Behind MATHBRAIN_TITLE_COVER, default OFF until measured here."""
    try:
        if not TITLE_COVER or not chosen or len(chosen) < 2:
            return chosen
        q = unicodedata.normalize("NFKD", prompt or "")
        q = "".join(c for c in q if not unicodedata.combining(c)).lower()
        want = [w for w in re.findall(r"[a-z]{3,}", q) if w not in _TITLE_COVER_STOP]
        if not want:
            return chosen

        def cover(h):
            t = unicodedata.normalize("NFKD", str(getattr(h.node, "title", "") or ""))
            t = "".join(c for c in t if not unicodedata.combining(c)).lower()
            tt = set(re.findall(r"[a-z]{3,}", t))
            if not tt:
                return 0.0
            hit = 0
            for w in want:
                if w in tt or (len(w) >= 5 and any(len(u) >= 5 and u[:5] == w[:5] for u in tt)):
                    hit += 1
            return hit / float(len(want))
        return [h for _, _, h in sorted(
            ((-cover(h), i, h) for i, h in enumerate(chosen)), key=lambda t: (t[0], t[1]))]
    except Exception:
        return chosen


def _name_cover_rerank(prompt, chosen):
    """Within the cards a block already carries, a card may pass another ONLY if its title covers EVERY name token
    of the question. Stable: full-coverage cards keep their own order ahead of the rest; with no name token, or no
    card covering all of them, nothing moves. Re-orders only, never adds, drops or rescores, so every gate that
    judged the block still holds.

    Why this variant (2026-09-13, 89-theorem bank, 178 questions, in-sample): plain 'more coverage wins' read +6
    rank-1 but split into +7 on questions that NAME the theorem and -1 on questions that describe it; this
    every-token form reads +5 on the named half and 0 on the unnamed half (129 -> 134 of 178). On the independent
    locate bank (110 reorderable rows, scored by node id) it changed no row - inert, not harmful. Behind
    MATHBRAIN_NAME_COVER (default 1 once the hook-path A/B and the regression gate confirmed it)."""
    try:
        if not chosen or len(chosen) < 2:
            return chosen
        want = _name_tokens(prompt)
        if not want:
            return chosen

        def _covers_all(h):
            t = unicodedata.normalize("NFKD", str(getattr(h.node, "title", "") or ""))
            t = "".join(c for c in t if not unicodedata.combining(c)).lower()
            tt = set(w for w in re.findall(r"[a-z0-9]+", t) if len(w) > 2)
            return want <= tt
        full = [h for h in chosen if _covers_all(h)]
        if not full or len(full) == len(chosen):
            return chosen
        return full + [h for h in chosen if not _covers_all(h)]
    except Exception:
        return chosen


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


def _trace_answers(stage, chosen):
    """stderr trace of the served ids at a stage, only under MATHBRAIN_TRACE_ANSWERS=1 (a harness aid, silent in production)."""
    if os.environ.get("MATHBRAIN_TRACE_ANSWERS", "0") == "1":
        try:
            sys.stderr.write("[trace-answers] %s: %s\n" % (stage, [h.node.id[:16] for h in (chosen or [])]))
        except Exception:
            pass


def _attach_answers(chosen, pool_hits, edges, cap_extra=2):
    """Serve the answer with the question.

    A served node that is the TARGET of a proves/refutes edge from a claim in the ranked pool brings that claim in
    ahead of it. Measured 2026-09-20 on his six results of the week (Aldous-Fill 4.48(a) and 4.49(ii), Bradley Q5,
    the maximal Kemeny constant, Ding-Lee-Peres 5.2, Ross's second conjecture): the store held every answer as a
    claim with a typed edge to the open-problem node, and four of the six questions were served the question node
    alone - the ranker prefers the shorter "Open problem: ..." card to a 700-1,100-character theorem statement.
    A question-shaped prompt asked of a store that holds the answer must serve the answer.

    RESTRICTED on purpose: only proves/refutes edges (never analogous_to, cites or semantic_association), only
    sources already in the pool (the closure in _prefilter_revision_nodes loads them), never a source that is
    itself superseded, at most `cap_extra` companions per block, and nothing is rescored or reordered among the
    cards the ranker chose. A served node with no such edge leaves the block byte-identical. Off with
    MATHBRAIN_ANSWER_COMPANION=0.
    """
    try:
        if not chosen or not edges or os.environ.get("MATHBRAIN_ANSWER_COMPANION", "1") == "0":
            return chosen
        from math_brain.models import EdgeKind
        by_target = {}
        superseded = set()
        for e in edges:
            if e.kind is EdgeKind.PROVES or e.kind is EdgeKind.REFUTES:
                by_target.setdefault(e.target_id, []).append(e.source_id)
            elif e.kind is EdgeKind.SUPERSEDES:
                superseded.add(e.target_id)
        if not by_target:
            return chosen
        pool = {h.node.id: h for h in (pool_hits or [])}
        served = {h.node.id for h in chosen}
        out, added = [], 0
        for h in chosen:
            for src in by_target.get(h.node.id, ()):
                if added >= cap_extra or src in served or src in superseded or src not in pool:
                    continue
                if str(getattr(pool[src].node, "kind", "") or "").lower() == "evidence":
                    continue
                out.append(pool[src])
                served.add(src)
                added += 1
            out.append(h)
        return out
    except Exception:
        return chosen


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
    hits = _title_exact_first(query, hits)
    chosen = [h for h in (_select(hits, title_query=query) or []) if float(ps.get(h.node.id, 0.0)) >= floor]
    return _attach_answers(chosen, result.hits, edges)


def _title_exact_first(query, hits):
    """When the sub-query IS a stored entry's title, that entry goes first.

    Measured 2026-09-13 on the L6 bank: of the cards missing from wide enumeration blocks, 73 of 73 were IN THE
    CANDIDATE POOL and simply not served - zero retrieval failures. Each named entry already gets its own
    sub-query and the merge takes the top hit of each, so a missing card means that sub-query's own top hit was
    something other than the entry the question named. Ranking by score is right for a question; it is wrong for
    a sub-query that is a verbatim title, where the answer is not "what scores highest near these words" but
    "the thing called this".

    The test is containment on distinctive words - the card's title must carry every one of the query's - which
    is the same rule the enumeration cap uses, so the two cannot disagree about whether the store has an entry.

    RESTRICTS, does not reorder, and that distinction is the whole function. `_select` re-sorts its input by
    rank score, so handing it a better order changes nothing - the lesson `_tuple_exact_first` below already
    records in its own docstring, and which I re-learned by writing the reordering version first and reading
    the neighbouring function afterwards. When some candidate is titled with the query, the choice is made
    AMONG those candidates; when none is, nothing changes and the ranker decides as before. Nothing is
    rescored and every gate downstream still judges whatever comes out.
    """
    try:
        qw = {w for w in re.findall(r"[a-z][a-z-]{2,}", str(query or "").lower()) if w not in _TITLE_STOP}
        if len(qw) < 2 or not hits:
            return hits
        exact = []
        for h in hits:
            tw = {w for w in re.findall(r"[a-z][a-z-]{2,}",
                                        str(getattr(h.node, "title", "") or "").lower())
                  if w not in _TITLE_STOP}
            if tw and qw <= tw:
                exact.append(h)
        return exact if exact else hits
    except Exception:
        return hits


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
        # HARNESS RUNS ONLY (2026-09-14). This gate fires on every non-mathematical prompt in every
        # session, so logging it in production would bury the file - which is why it logged nothing at
        # all, and why it has been invisible. The cost of that invisibility, measured today: of the 12
        # answerable bank questions the hook silences, FIVE die right here and never reach the log, so
        # the silence file accounted for seven and read as though the margin test were the whole story.
        # The five are not marginal cases - "Which finite graphs are planar, in terms of forbidden
        # subdivisions?", "Asymptotics of the number of primes up to x.", Wald's identity, Jensen's
        # inequality written in symbols, Menger's theorem stated in words. A harness session (no uuid)
        # now gets the row; a real session still pays nothing.
        # SIZING, so nobody reads this as a call to loosen the gate: it refuses 5 of 178 answerable
        # bank questions (2.8 pct) and 4 of 40 absent-target ones (10 pct), so it is three times more
        # likely to stop a question the store cannot answer than one it can. Widening it trades those
        # five against a false-fire rate already at 60-67 pct. Measure both arms before touching it.
        _silence_note(data, "math_gate_rejected", harness_only=True)
        return
    if not os.path.exists(STORE_PATH):
        # A math prompt arrived and there is no store to answer it from. Logged since 2026-09-13: this is the
        # one silent exit that means the brain is BROKEN rather than merely quiet, and it was indistinguishable
        # from an ordinary abstention. The gate above it stays silent on purpose - it fires on every non-math
        # prompt and would bury the log.
        _silence_note(data, "store_missing")
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
                        _silence_note(data, "coverage_gate_prefilter:%.3f" % (max(_ps0.values()) / _qs0))
                        return
            except Exception:
                pass
        with ResearchStore.open_read_only(STORE_PATH) as store:
            # Keep node, revision-edge, and review reads in one short snapshot.
            store.connection.execute("BEGIN")
            if prefilter_ids is not None:
                if not prefilter_ids:
                    _silence_note(data, "prefilter_empty")
                    return
                nodes, edges = _prefilter_revision_nodes(store, prefilter_ids)
            else:
                nodes = list(store.iter_nodes())
                edges = list(store.iter_edges())
            reads = _read_records(store)
        if not nodes:
            _silence_note(data, "no_nodes")
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
            _silence_note(data, "no_hits")
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
        # ARGUMENT CARD BUDGET (MATHBRAIN_ARG_CARDS, default 4 since 2026-09-13). A prompt carrying a numbered argument
        # asks about several results at once - the 77 hard proof-audit items name 4.0 results each - while a lookup
        # question asks about one. Only argument-shaped prompts widen; every other prompt keeps three, so this dial
        # cannot add a card to a lookup and cannot move the absent-target fire rate.
        #
        # WHY 4, measured 2026-09-13 on those 77 items (same instrument, same bank, one run per cell):
        #     hook        cap   cards landed   block chars   named results reached
        #     verbose      3        2.86           2470          142/310   <- what shipped until today
        #     verbose      4        3.03           2613          145/310
        #     compressed   3        2.87           1965          142/310
        #     compressed   4        3.68           2484          160/310   <- this
        #     compressed   5        3.74           2526          151/310
        # The cap was never the binding constraint - the BUDGET was. A verbose card cost ~845 characters, so three of
        # them plus the old 407-character header filled the 3,000-char block and the assembler (whole cards only) threw
        # the fourth away: asking for four landed 3.03. Cutting the scaffolding (see the header and _trust_tag) made a
        # fourth card affordable, and it is worth 18 more named results than the shipped setting for 14 more characters.
        # Five is worse than four: the block barely grows (3.74 cards) but the extra slot changes WHICH cards are
        # selected and displaces a better one - more candidates, less reach.
        try:
            _argc = int(os.environ.get("MATHBRAIN_ARG_CARDS", "4") or 4)
        except Exception:
            _argc = 4
        _cap = _argc if (_argc > 3 and ARGUMENT_SHAPE.search(prompt)) else 3
        # A QUESTION THAT NAMES N ENTRIES NEEDS N CARDS (MATHBRAIN_ENUM_WIDTH, default 10 = the most it will
        # widen to; 0 disables). Until 2026-09-13 every lookup prompt got exactly three cards, because the card
        # budget above only widens on ARGUMENT_SHAPE. That is fine when a question asks about one thing and a
        # coincidence when it asks about three - and a silent, total failure when it asks about four.
        #
        # Measured on a bank built to find this (L6, 240 items asking for the sum of N ledger pages, N = 3..8;
        # every page lives in one card's body, so the item is answerable exactly when the block carries all N):
        #     N = 3   37/40 answerable   2.92 cards served
        #     N = 4    0/40              2.90
        #     N = 5    0/40              2.75
        #     N = 6    0/40              2.90
        #     N = 7    0/40              2.85
        #     N = 8    0/40              2.95
        # A cliff, not a slope. The block serves ~2.9 cards however many the question needs, so a question
        # naming four or more stored entries cannot be answered from it at all. The 1,000-item A/B scored the
        # page families 100 pct because those items name exactly THREE problems and the cap is exactly three;
        # the perfect score was the bank agreeing with the dial, not the brain having no ceiling.
        #
        # The quoted-title extractor already counts what the prompt names, so the cap can simply follow it. Only
        # prompts that explicitly name several stored entries widen - which is exactly when the reader wants them
        # all - and the block budget below widens with it, or the whole-cards-only assembler would throw away
        # everything past the third card and nothing would change.
        try:
            _enum_max = int(os.environ.get("MATHBRAIN_ENUM_WIDTH", "10") or 10)
        except Exception:
            _enum_max = 10
        # WIDEN FOR ENTRIES THE STORE HAS, NOT FOR QUOTATION MARKS. Counting quoted titles was the first
        # version and it spent the owner's context on questions the brain cannot answer: measured on a control
        # bank of 80 enumeration questions whose every quoted title is ABSENT from the store (built by
        # recombining halves of real ledger titles, each checked absent on its full text), the hook fired on
        # 80 of 80 and the first version scaled the damage with the question - 3.0 cards and 2,408 characters
        # at three names, 8.0 cards and 6,008 at eight. Being wrong at eight cards is not better than being
        # wrong at three. So the cap follows the number of named entries the CANDIDATE POOL can actually cover:
        # on the L6 bank every name matches a card and the block widens; on the absent-title control nothing
        # matches and it stays at three, which is where it was before any of this.
        _named_entries = 0
        _quoted_n = 0
        _covered_titles = []   # the quoted names the pool can actually cover, IN ORDER - see the sub-query stage
        if _enum_max > 0 and not pinned_ids:
            try:
                _quoted = _quoted_subqueries(prompt, limit=_enum_max)
                _quoted_n = len(_quoted)
                # THREE, not four. The count is wanted for two different decisions - widen the cap above three,
                # and abstain at zero - and computing it only when the cap would widen is a trap: at exactly
                # three quoted titles it would stay 0 and the abstention below would silence every three-title
                # question, which is the 473 items of the 1,000-item bank the brain answers perfectly.
                if _quoted_n >= 3:
                    _pool_titles = [({w for w in re.findall(r"[a-z][a-z-]{2,}",
                                                            str(getattr(h.node, "title", "") or "").lower())
                                      if w not in _TITLE_STOP}) for h in _hits_for_select]
                    for _q in _quoted:
                        _qw = {w for w in re.findall(r"[a-z][a-z-]{2,}", _q.lower()) if w not in _TITLE_STOP}
                        if not _qw:
                            continue
                        # CONTAINMENT, not overlap. "Shares two distinctive words" looked reasonable and was
                        # useless here: the control's absent titles are built by recombining halves of two real
                        # titles, so they share plenty of words with real cards and every one of them passed.
                        # The store HAS an entry only when some card's title carries every distinctive word of
                        # the name asked for - which is exactly how the control bank defines absence, so the two
                        # cannot disagree by construction.
                        if any(_qw <= _tw for _tw in _pool_titles):
                            _named_entries += 1
                            _covered_titles.append(_q)
                if _named_entries > _cap:
                    _cap = min(_named_entries, _enum_max)
            except Exception as _exc:
                _silence_note(data, "enum_width_error:%s" % type(_exc).__name__)
        # NOTHING IT ASKED FOR IS HERE (MATHBRAIN_ENUM_ABSTAIN, default 1). A question that quotes three or more
        # stored-entry titles and whose every quoted title is missing from the candidate pool is asking about
        # things this store does not hold, and serving three vocabulary neighbours answers nobody. Measured on a
        # control bank of 80 such questions - titles built by recombining halves of real ledger entries, each
        # verified absent on its full text, by a detector itself controlled against known-present titles - the
        # hook served a block on 80 of 80. His 2026-09-02 order is that the brain fire less and be surer when it
        # does, and this is the clearest case of firing on nothing that any bank here has produced.
        #
        # Narrow by construction, and the scoping was checked before the rule was written: of the 1,000-item
        # bank, only op_oddsource, op_pagemax and op_pagesum quote three or more long titles at all (473 items,
        # every one of which the brain answers), and no proof-audit item, no saw_* item, and nothing in either
        # half of the fresh bank does. A question with even ONE of its quoted titles in the pool still serves.
        if (os.environ.get("MATHBRAIN_ENUM_ABSTAIN", "1") != "0"
                and _quoted_n >= 3 and _named_entries == 0 and not pinned_ids):
            _silence_note(data, "enum_none_of_%d_titles_in_store" % _quoted_n)
            return
        # DEFINITIONS DO NOT DECIDE STEPS - AND DEMOTING THEM STILL LOSES (MATHBRAIN_DEFN_DEMOTE, default 0 = OFF;
        # 2026-09-13). Kept as a measured negative so the next person to notice the 8 pct hit rate does not spend
        # the afternoon I spent. The diagnosis below is solid; the fix does not work. Measured across the 77
        # proof-audit items' 280 served slots: a definition card took 38 of them and named a result the argument
        # actually uses in 3 - an 8 pct hit rate against 53 for theorem, 49 for concept, 30 for lemma. That is
        # 12.5 pct of the block's capacity spent on cards that match the proof's VOCABULARY and say nothing about
        # whether a step follows. It happens because a proof-audit prompt is ~10,000 characters of mathematics in
        # which the four named results are a tiny share of the token mass, so BM25 over the whole prompt favours
        # definitions of the words it repeats.
        #
        # The existing MATHBRAIN_KIND_PREF reorders the CHOSEN cards and cannot help: measured at 121 -> 122 of
        # 280, because reordering never changes the set. `_select` re-sorts its input by rank score, so a
        # definition also cannot be demoted by handing it over later - it has to leave the candidate pool.
        # It leaves only when something else can fill the slot: if there are not enough non-definition candidates
        # to fill the cap, the definitions stay and nothing is lost. Argument-shaped prompts only - on a lookup
        # ("what is a tight family of measures?") the definition IS the answer.
        #
        # AND IT MEASURES WORSE: 155/310 with the demotion against 160/310 with it off, same instrument and bank,
        # back to back. The arithmetic that predicted +17 assumed the freed slots would be filled at the theorem
        # rate; they are not. The mechanism is almost certainly that this pool feeds gates computed RELATIVE TO
        # THE TOP CARD - the margin test's leader and runner-up, the campaign-family exemption, the scaled
        # on-topic floor - so removing candidates moves the leader and changes which rivals exist, and a block
        # that was served can go silent or lose a good card for reasons that have nothing to do with definitions.
        # The lesson for the next attempt: in this hook a candidate is not just a card, it is part of the
        # comparison that decides whether ANY card goes out. Filtering the pool is not a neutral operation, and a
        # kind-aware selector has to work inside `_select` alongside those gates rather than upstream of them.
        if (os.environ.get("MATHBRAIN_DEFN_DEMOTE", "0") != "0"
                and _cap > 3 and ARGUMENT_SHAPE.search(prompt) and not pinned_ids):
            try:
                _nondef = [h for h in _hits_for_select
                           if str((_node_meta(h.node).get("claim_kind")
                                   or _node_meta(h.node).get("kind")
                                   or getattr(h.node, "kind", "") or "")).lower() != "definition"]
                if len(_nondef) >= _cap:
                    _hits_for_select = _nondef
            except Exception:
                pass
        chosen = _select(_hits_for_select, cap=_cap, prompt=prompt)
        if pinned_ids:
            pin = [h for h in result.hits if h.node.id in pinned_ids][:2]
            rest = [h for h in (chosen or []) if h.node.id not in pinned_ids]
            chosen = (pin + rest)[:_cap] if pin else chosen
        elif os.environ.get("MATHBRAIN_NAME_COVER", "1") != "0":
            # NAME COVERAGE (2026-09-13): the sibling-theorem miss - Matrix Bernstein asked, scalar Bernstein
            # served first with the named card at rank 2. A card that carries EVERY name token of the question
            # moves ahead of cards that do not; nothing else changes (see _name_cover_rerank).
            chosen = _name_cover_rerank(prompt, chosen)
            chosen = _title_cover_rerank(prompt, chosen)
            chosen = _defn_below_result_rerank(chosen)
        # COVERAGE SWAP (MATHBRAIN_COVER_SWAP, default 1; 2026-09-13). An argument that names four results wants
        # one card per result, and `_select` is not built to deliver that: it ranks by score, so a prompt whose
        # strongest signal is one theorem can spend three of four slots on that theorem's neighbourhood while the
        # other three named results go unserved.
        #
        # Measured on the 77 proof-audit items, splitting all 310 named results by WHERE the miss happens:
        #     served in the block  160  52%
        #     in pool, not served   94  30%   <- this: the ranker HAD the card and chose something else
        #     never retrieved       56  18%   <- the prefilter's problem, not the selector's
        # So the pool already holds cards for 94 results the block never shows, and perfect selection would read
        # 254/310 rather than 160.
        #
        # This is a SWAP, deliberately, not a filter. Filtering definitions out of the candidate pool measured
        # WORSE (155 against 160) because the pool also supplies the comparisons the margin test, the campaign
        # exemption and the scaled on-topic floor run on - remove a candidate and you move the leader and change
        # whether anything is served at all. So the pool is left untouched, `_select` decides as before, and only
        # then do slots that cover NO named target give way to pool cards that cover one. Every swapped card still
        # faces the on-topic gate, the integrity read and the dedup below, exactly as a chosen card would.
        # MEASURED AND OFF (default 0 since the same day it was written). The swap fires - 2 to 3 swaps on a
        # typical argument item - and moves the metric not at all: 160/310 with it and 160 without at the shipped
        # budget, 162 and 162 at 3,400 characters, card titles bit-identical. The reason is upstream of the idea:
        # the swap can only cover targets the EXTRACTOR names, and the extractor finds one to three "X theorem"
        # shaped names per item while these arguments lean on about four results, many of them descriptive
        # ("upper semicontinuity of an infimum of continuous functions") that no name-shaped regex will ever
        # catch. Coverage selection is not wrong; it is starved of targets. Left in, defaulted off, because the
        # day the target list gets better this is the machinery that spends it - see COVERAGE_CEILING in the
        # campaign record for what it is worth with perfect targets.
        # ON for enumeration prompts, whatever the dial says. The swap was a wash on argument prompts because
        # four slots were already full of cards that earn, so covering one target meant dropping another. An
        # enumeration prompt is the opposite case and the one where this machinery finally pays: the cap has just
        # been widened to the number of entries the question NAMES, so the seats exist, and the targets are not
        # guessed from prose - the question quotes them. Widening alone is not enough; measured on two L6 items
        # it seated 6 and 8 cards and still carried only 4 of the needed pages, because the ranker fills the new
        # seats by score. This puts the named entries in them.
        _enum_cover = _named_entries > 3
        if (chosen and not pinned_ids
                and (_enum_cover
                     or (os.environ.get("MATHBRAIN_COVER_SWAP", "0") != "0"
                         and _cap > 3 and ARGUMENT_SHAPE.search(prompt)))):
            try:
                if _enum_cover:
                    # the question quotes exactly what it wants; nothing else belongs in the target list
                    _targets = list(_quoted_subqueries(prompt, limit=_enum_max))
                else:
                    _targets = list(_named_result_subqueries(prompt)) + list(_quoted_subqueries(prompt))
                # INVERTED TARGETS (MATHBRAIN_COVER_INVERT, default 1 when the swap runs). Extracting names from
                # 10,000 characters of prose is open-ended and the regex only catches "X theorem" shapes, so it
                # finds one to three targets where the argument leans on four and misses every descriptive result.
                # Turn the question round: the pool is ~300 cards with known titles, so instead of guessing what
                # the prompt names, ask of each card whether the prompt SAYS what that card is about. A membership
                # test over a known vocabulary, no pattern needed, and descriptive titles - the ones the regex
                # cannot see - carry the most distinctive words and match the most reliably. Measured on the title
                # channel across the 77 items: 45 as it ships, 67 inverted, 135 with oracle targets.
                # Not on an enumeration prompt: there the question quotes its own targets exactly, so guessing
                # more of them can only displace one the reader actually asked for.
                if not _enum_cover and os.environ.get("MATHBRAIN_COVER_INVERT", "1") != "0":
                    _pw = {w for w in re.findall(r"[a-z][a-z-]{2,}", prompt.lower()) if w not in _TITLE_STOP}
                    _cands = []
                    for _h in _hits_for_select:
                        _tw = {w for w in re.findall(r"[a-z][a-z-]{2,}",
                                                     str(getattr(_h.node, "title", "") or "").lower())
                               if w not in _TITLE_STOP}
                        if len(_tw) < 2:
                            continue
                        # every distinctive word of the title is present in the prompt
                        if len(_tw & _pw) >= 2 and len(_tw & _pw) == len(_tw):
                            _cands.append((len(_tw), str(getattr(_h.node, "title", "") or "")))
                    _cands.sort(key=lambda x: -x[0])
                    _targets = [t for _, t in _cands[:6]] + _targets
                if _targets:
                    def _content(s):
                        return {w for w in re.findall(r"[a-z][a-z-]{2,}", str(s or "").lower())
                                if w not in _TITLE_STOP}

                    def _covers(node, target):
                        """Does this card's TITLE name that result? Deliberately NOT the grader's rule.

                        The instrument matches a name's first two content words; mirroring that here would be
                        tuning the hook to its own scoreboard. This asks a plainer question - do the target's
                        distinctive words appear in the title - and lets the instrument judge it at arm's length.
                        """
                        tw, nw = _content(getattr(node, "title", "")), _content(target)
                        if not nw or not tw:
                            return False
                        shared = tw & nw
                        return len(shared) >= 2 or (len(nw) == 1 and bool(shared))

                    _covered = lambda h: any(_covers(h.node, t) for t in _targets)
                    _ids = {h.node.id for h in chosen}
                    _swaps = 0
                    if os.environ.get("MATHBRAIN_SWAP_DEBUG"):
                        sys.stderr.write("[cover-swap] targets=%d chosen=%d pool=%d uncovered=%d victims=%d\n"
                                         % (len(_targets), len(chosen), len(_hits_for_select),
                                            sum(1 for t in _targets if not any(_covers(h.node, t) for h in chosen)),
                                            sum(1 for h in chosen if not _covered(h))))
                    for _t in _targets:
                        if any(_covers(h.node, _t) for h in chosen):
                            continue
                        _cand = next((h for h in _hits_for_select
                                      if h.node.id not in _ids and _covers(h.node, _t)), None)
                        if _cand is None:
                            continue
                        _victims = [h for h in chosen if not _covered(h)]
                        if not _victims:
                            break
                        _victim = _victims[-1]
                        chosen = [_cand if h is _victim else h for h in chosen]
                        _ids.discard(_victim.node.id)
                        _ids.add(_cand.node.id)
                        _swaps += 1
                    if os.environ.get("MATHBRAIN_SWAP_DEBUG"):
                        sys.stderr.write("[cover-swap] swaps made=%d\n" % _swaps)
            except Exception as _exc:
                # Never silently. A swallowed NameError in this file once turned the whole on-topic gate into a
                # no-op and invalidated three measurements before anyone noticed; a stage that fails must say so.
                _silence_note(data, "cover_swap_error:%s: %s" % (type(_exc).__name__, _exc))
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
        # the answer rides with its question (see _attach_answers) - placed AFTER the on-topic gate and the rescue on
        # purpose: the gate keeps a card only above GATE_RATIO x the prefilter's top score, and a theorem statement
        # scores below the question it settles on the question's own words (traced 2026-09-20 on the Ross question:
        # _select ranked the counterexample first and the gate removed it). The edge is the on-topic evidence.
        chosen = _attach_answers(chosen, result.hits, edges)
        _trace_answers('after_attach', chosen)
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
                    # One sub-query per title the question quotes, not a fixed four. The default limit of 4 was
                    # invisible while every bank item named three entries; on the L6 bank it is the whole ceiling.
                    # A question naming eight problems ran four sub-queries, so four of its eight cards could
                    # never enter the pool and the block carried 4 of 8 pages however many slots it was given -
                    # widening the cap alone moved N=4 from 0/40 to answerable and left N=6 and N=8 stuck at 4.
                    #
                    # ASK FOR THE TITLES THE STORE HAS, NOT THE FIRST N OF THEM. Passing a COUNT was wrong
                    # whenever the quoted names are a mix: `_quoted_subqueries` returns the first N titles
                    # POSITIONALLY, so a question naming two problems the store lacks and three it holds - the
                    # ordinary shape when somebody misremembers a name - spent its sub-queries on the dead ones
                    # and never asked for the live ones. Measured on a bank built for it (L9, absent names
                    # deliberately first): the block served every time, correctly, and carried the pages of
                    # only 22 of 59 present titles at a flat 3.0 cards. The covered titles are collected above
                    # in the order they appear, so here they are simply used.
                    subs = _covered_titles or _quoted_subqueries(prompt, limit=4)
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
            _silence_note(data, "no_chosen_after_select")
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
        _trace_answers('after_consistent', chosen)
        chosen = order_selected(prompt, chosen, pinned_ids)
        _trace_answers('after_order', chosen)
        if not chosen:
            _silence_note(data, "no_chosen_after_order")
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
                        _silence_note(data, "cardcov_gate:%.3f" % (sum(_idf(t) for t in _qt & _ct) / _w))
                        return
            except Exception:
                pass
        sess_for_guard = data.get("session_id") or ""
        dup_ids = _already_injected(sess_for_guard, [h.node.id for h in chosen], record=False)
        # AN EXPLICITLY NAMED ENTRY IS NEVER A DUPLICATE (2026-09-13). The guard stops the hook re-injecting a
        # card the session has already seen, which is right for a session that keeps brushing the same topic by
        # accident. It is wrong when the question NAMES what it wants: measured on the L6 bank, asking the same
        # enumeration question twice down one session returned 5 cards and 5 of 5 pages the first time and ZERO
        # the second - total starvation, on a question whose cards ARE its answer. A different enumeration
        # question in the same session was unaffected (3 of 5 both ways), so the guard only bites on a repeat,
        # and a repeat is exactly when the reader is asking again because they no longer have the first block -
        # after a compaction, or to check a figure. Suppressing it serves nobody. Narrow on purpose: only
        # prompts that quote several stored entries, and the ids are still RECORDED below, so an ordinary lookup
        # in the same session is deduplicated exactly as before.
        if _named_entries > 3:
            dup_ids = set()
        chosen = [h for h in chosen if h.node.id not in dup_ids]
        if not chosen:
            _silence_note(data, "all_dedup_suppressed:%d" % len(dup_ids))
            return
        pieces = []
        for hit in chosen:
            node = hit.node
            meta = _node_meta(node)
            kind = str(meta.get("claim_kind") or meta.get("kind") or node.kind)
            label, text = _render_parts(node, query=prompt, max_text_chars=MAX_STATEMENT_CHARS)
            hint = _trust_tag(meta, reads.get(node.id))
            if _structure_flattened(text):
                hint = "notation flattened; " + hint
            if _source_is_excerpt(node, text):
                hint = "EXCERPT, incomplete; " + hint
            pieces.append((node.id,
                "- %s [%s]: %s\n    (%s | %s)"
                % (label, kind, text, node.id, hint)
            ))
        # Owner order 2026-09-02 ("a note can mean something good or bad, not necessarily good ... the
        # brain can give a hint"): the block frames every note as a lead that can cut either way. The
        # B12 bank measured why this matters - a served statement about the cited result made a wrong
        # citation or an over-extended scope look legitimate (citation/scope/sharpness faults lost ground
        # under notes while dropped-hypothesis faults gained).
        # 2026-09-13: said in 248 characters instead of 407, every clause kept - hint-not-verdict, cuts both
        # ways, a note is not evidence the step uses the result correctly, nothing verified, how to read the
        # full note. Measured on the 1,000 captured blocks of the 09-12 A/B: the header cost 407 chars of a
        # 3,000-char budget on EVERY block, and 487 of 999 blocks sat at the cap - so the boilerplate was
        # displacing mathematics on half the questions. See _trust_tag for the per-card half of the same cut.
        header = (
            "[math-brain] leads, not verdicts - a match can expose a step as easily as support it, and none "
            "of this is independently verified: check each result's hypotheses against the step that uses it. "
            "Full note: python ~/.claude/dreaming/math_note.py SOURCE_ID"
        )
        # Whole rendered cards only. Source excerpts remain explicitly labeled.
        # The budget follows the cap. `_assemble_note_block` emits whole cards only, so a block sized for three
        # cards silently discards the fourth onward and widening the cap alone would change nothing (measured:
        # asking the old verbose hook for four cards landed 3.03). A compressed card runs about 720 characters,
        # so the room a widened prompt needs is the header plus that per card. Only prompts that named several
        # stored entries are widened; every ordinary prompt keeps MAX_BLOCK_CHARS exactly as before.
        _budget = MAX_BLOCK_CHARS
        if _named_entries > 3 and len(pieces) > 3:
            _budget = max(MAX_BLOCK_CHARS, len(header) + 760 * len(pieces))
        block, emitted_ids = _assemble_note_block(header, pieces, _budget)
        if not block:
            _silence_note(data, "empty_block:%d_pieces" % len(pieces))
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


def _silence_note(data, reason, harness_only=False):
    """One line per silent or degraded exit, so 'how often does the brain go mute under load' has an answer.

    Format: epoch  session-prefix  reason. Never raises; never blocks the prompt.

    HARNESS RUNS GO TO A SEPARATE FILE (2026-09-13). A real session carries a uuid session id, which is the test
    `surfaced.tsv` beside this one has always applied; this log had no such test, so every measurement run landed
    in it. Counted live that day: 256 rows, of which ONE was a real session - the other 255 were the campaign's
    own probes, mine among them, several hundred in an afternoon, and the serial load pushed the hook past its
    time budget so the log read as if the brain were constantly timing out in production. A log that answers
    "how often does this happen to real users" must not be written by the instrument measuring it. Harness rows
    are kept rather than dropped - they are how a failing stage gets debugged - but in their own file."""
    try:
        sess = str((data or {}).get("session_id") or "")
        real = bool(re.match(r"^[0-9a-f]{8}-", sess))
        if harness_only and real:
            return          # a stage that fires on ordinary traffic: diagnosable in a harness, silent in production
        name = "mathbrain_silence.tsv" if real else "mathbrain_silence_harness.tsv"
        # A FOURTH COLUMN: which QUESTION went quiet (2026-09-14). Without it this log gives the
        # population and not the attribution, and the difference is a wrong diagnosis: on 09-14 five
        # coverage-gate rows in one window led me to blame that gate for four silences it had nothing
        # to do with - measured directly, all four passed it comfortably and the two it really silenced
        # were absent-target questions it was right to silence. A harness can now map a row back to its
        # bank item, and a real session can be traced without this file ever holding what was typed:
        # the fingerprint is a hash, so the log stays as safe to read as it was.
        _fp = ""
        try:
            _p = str((data or {}).get("prompt") or "")
            if _p:
                _fp = hashlib.sha1(_p.encode("utf-8", "replace")).hexdigest()[:10]
        except Exception:
            _fp = ""
        with io.open(os.path.join(HOME, ".claude", "dreaming", name), "a", encoding="utf-8") as h:
            h.write("%.0f\t%s\t%s\t%s\n" % (time.time(), sess[:12], reason, _fp))
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
            if _structure_flattened(text):
                hint = "notation flattened (fractions/exponents lost in the harvest); " + hint
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
